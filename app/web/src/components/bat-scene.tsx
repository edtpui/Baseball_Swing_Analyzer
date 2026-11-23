import { useEffect, useRef } from "react"
import * as THREE from "three"
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js"
import { Line2 } from "three/examples/jsm/lines/Line2.js"
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js"
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js"

import { batDirection, rotateAboutVertical } from "@/lib/format"
import type { LiveData, SwingRecord, Theme, Vec3 } from "@/lib/types"

export interface Playback {
  /** Fractional index into the swing's path arrays. */
  index: number
  playing: boolean
  /** 1 = real time. */
  speed: number
}

interface BatSceneProps {
  mode: "live" | "replay"
  liveRef: React.RefObject<LiveData | null>
  swing: SwingRecord | null
  batLengthM: number
  sensorFacesOut: boolean
  theme: Theme
  playbackRef: React.RefObject<Playback>
  /** Rotation about the vertical applied to the live view, set by "Face forward". */
  yawOffsetRef: React.RefObject<number>
}

// The sensor cannot know its height above the ground, so the hands are drawn
// at a typical contact height. Only the shape of the path is measured.
const HAND_HEIGHT_M = 1.0
const END_HOLD_S = 0.8
const FAN_STRIDE = 2
const UP = new THREE.Vector3(0, 1, 0)

/** World frame is Z up; three.js is Y up. */
function toScene(x: number, y: number, z: number): THREE.Vector3 {
  return new THREE.Vector3(x, z, -y)
}

function cssColor(name: string): THREE.Color {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return new THREE.Color(value || "#888888")
}

function batGeometry(length: number): THREE.LatheGeometry {
  const profile: [number, number][] = [
    [0, 0],
    [0.026, 0],
    [0.026, 0.018],
    [0.014, 0.036],
    [0.0125, 0.28 * length],
    [0.016, 0.45 * length],
    [0.027, 0.62 * length],
    [0.033, 0.74 * length],
    [0.033, length - 0.03],
    [0.027, length - 0.008],
    [0, length],
  ]
  return new THREE.LatheGeometry(
    profile.map(([r, y]) => new THREE.Vector2(r, y)),
    28,
  )
}

function groundGrid(extent: number, cell: number): THREE.BufferGeometry {
  const points: number[] = []
  for (let v = -extent; v <= extent + 1e-6; v += cell) {
    points.push(-extent, v, 0, extent, v, 0, v, -extent, 0, v, extent, 0)
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3))
  return geometry
}

function arrowGeometry(): THREE.BufferGeometry {
  const z = 0.003
  const points = [0.35, 0, z, 1.25, 0, z, 1.25, 0, z, 1.1, 0.07, z, 1.25, 0, z, 1.1, -0.07, z]
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3))
  return geometry
}

interface ReplayObjects {
  group: THREE.Group
  sweetPre: Line2
  sweetPost: Line2
  knobPre: Line2
  knobPost: Line2
  fan: THREE.LineSegments
  ball: THREE.Mesh
  knob: Vec3[]
  dir: Vec3[]
  contact: number
  sampleDt: number
  impact: boolean
}

interface SceneHandles {
  renderer: THREE.WebGLRenderer
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  world: THREE.Group
  grid: THREE.LineSegments
  arrow: THREE.LineSegments
  pivot: THREE.Group
  batMesh: THREE.Mesh
  batMaterial: THREE.MeshStandardMaterial
  puckMaterial: THREE.MeshStandardMaterial
  lineMaterials: LineMaterial[]
  replay: ReplayObjects | null
}

function fatLine(points: Vec3[], material: LineMaterial): Line2 {
  const geometry = new LineGeometry()
  const flat = points.length >= 2 ? points.flat() : [0, 0, 0, 0, 0, 0]
  geometry.setPositions(flat)
  const line = new Line2(geometry, material)
  line.computeLineDistances()
  line.frustumCulled = false
  return line
}

function disposeReplay(handles: SceneHandles) {
  const replay = handles.replay
  if (!replay) return
  handles.world.remove(replay.group)
  for (const line of [replay.sweetPre, replay.sweetPost, replay.knobPre, replay.knobPost]) {
    line.geometry.dispose()
    line.material.dispose()
  }
  replay.fan.geometry.dispose()
  ;(replay.fan.material as THREE.Material).dispose()
  replay.ball.geometry.dispose()
  ;(replay.ball.material as THREE.Material).dispose()
  handles.lineMaterials = []
  handles.replay = null
}

function buildReplay(handles: SceneHandles, swing: SwingRecord, size: THREE.Vector2) {
  disposeReplay(handles)
  const { knob, dir, contact_index: contact } = swing.path
  if (knob.length < 2) return

  const sweetDistance = swing.bat.length_m - swing.bat.sweet_spot_from_tip_m
  const lift = (p: Vec3): Vec3 => [p[0], p[1], p[2] + HAND_HEIGHT_M]
  const along = (i: number, d: number): Vec3 =>
    lift([knob[i][0] + dir[i][0] * d, knob[i][1] + dir[i][1] * d, knob[i][2] + dir[i][2] * d])
  const sweet = knob.map((_, i) => along(i, sweetDistance))
  const hands = knob.map(lift)

  const material = (colorVar: string, dashed: boolean) => {
    const m = new LineMaterial({
      color: cssColor(colorVar),
      linewidth: dashed ? 2 : 3,
      dashed,
      dashSize: 0.04,
      gapSize: 0.03,
      transparent: dashed,
      opacity: dashed ? 0.7 : 1,
    })
    m.resolution.copy(size)
    m.userData.colorVar = colorVar
    handles.lineMaterials.push(m)
    return m
  }

  const group = new THREE.Group()
  const sweetPre = fatLine(sweet.slice(0, contact + 1), material("--series-bat", false))
  const sweetPost = fatLine(sweet.slice(contact), material("--series-bat", true))
  const knobPre = fatLine(hands.slice(0, contact + 1), material("--series-hand", false))
  const knobPost = fatLine(hands.slice(contact), material("--series-hand", true))

  const fanPoints: number[] = []
  for (let i = 0; i <= contact; i += FAN_STRIDE) {
    fanPoints.push(...hands[i], ...along(i, swing.bat.length_m))
  }
  const fanGeometry = new THREE.BufferGeometry()
  fanGeometry.setAttribute("position", new THREE.Float32BufferAttribute(fanPoints, 3))
  const fan = new THREE.LineSegments(
    fanGeometry,
    new THREE.LineBasicMaterial({ color: cssColor("--ink-muted"), transparent: true, opacity: 0.28 }),
  )
  fan.frustumCulled = false

  const ball = new THREE.Mesh(
    new THREE.SphereGeometry(0.037, 20, 14),
    new THREE.MeshStandardMaterial({ color: cssColor("--ink"), roughness: 0.6 }),
  )
  ball.position.set(...sweet[contact])

  group.add(fan, sweetPre, sweetPost, knobPre, knobPost, ball)
  handles.world.add(group)

  const t = swing.series.t_ms
  handles.replay = {
    group,
    sweetPre,
    sweetPost,
    knobPre,
    knobPost,
    fan,
    ball,
    knob,
    dir,
    contact,
    sampleDt: t.length > 1 ? (t[1] - t[0]) / 1000 : 1 / swing.sample_rate_hz,
    impact: swing.metrics.impact,
  }
}

export function BatScene(props: BatSceneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const propsRef = useRef(props)
  propsRef.current = props

  // Scene lifetime: created once, torn down on unmount.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setClearColor(0x000000, 0)
    container.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, 1.6))
    const sun = new THREE.DirectionalLight(0xffffff, 1.4)
    sun.position.set(2, 4, 3)
    scene.add(sun)

    const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 50)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.enablePan = false
    controls.minDistance = 0.8
    controls.maxDistance = 9
    controls.maxPolarAngle = Math.PI * 0.52

    const world = new THREE.Group()
    world.rotation.x = -Math.PI / 2
    scene.add(world)

    const grid = new THREE.LineSegments(groundGrid(2.5, 0.5), new THREE.LineBasicMaterial())
    const arrow = new THREE.LineSegments(arrowGeometry(), new THREE.LineBasicMaterial())
    world.add(grid, arrow)

    const batMaterial = new THREE.MeshStandardMaterial({ roughness: 0.55, metalness: 0.05, transparent: true })
    const puckMaterial = new THREE.MeshStandardMaterial({ roughness: 0.4 })
    const pivot = new THREE.Group()
    const batMesh = new THREE.Mesh(batGeometry(propsRef.current.batLengthM), batMaterial)
    const puck = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.012, 20), puckMaterial)
    puck.position.y = -0.006
    pivot.add(batMesh, puck)
    pivot.position.set(0, 0, HAND_HEIGHT_M)
    world.add(pivot)

    const handles: SceneHandles = {
      renderer,
      camera,
      controls,
      world,
      grid,
      arrow,
      pivot,
      batMesh,
      batMaterial,
      puckMaterial,
      lineMaterials: [],
      replay: null,
    }
    handlesRef.current = handles

    const size = new THREE.Vector2(1, 1)
    const resize = () => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      size.set(width, height)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      for (const m of handles.lineMaterials) m.resolution.copy(size)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(container)
    resize()

    const direction = new THREE.Vector3()
    let last = performance.now()
    let hold = 0
    let frame = 0

    const render = (now: number) => {
      frame = requestAnimationFrame(render)
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const p = propsRef.current
      const replay = handles.replay

      if (p.mode === "replay" && replay) {
        const playback = p.playbackRef.current
        const lastIndex = replay.knob.length - 1
        if (playback.playing) {
          if (playback.index >= lastIndex) {
            hold += dt
            if (hold >= END_HOLD_S) {
              hold = 0
              playback.index = 0
            }
          } else {
            hold = 0
            playback.index = Math.min(lastIndex, playback.index + (dt * playback.speed) / replay.sampleDt)
          }
        }
        const index = Math.min(lastIndex, Math.max(0, playback.index))
        const i = Math.min(lastIndex - 1, Math.floor(index))
        const f = index - i
        const a = replay.knob[i]
        const b = replay.knob[i + 1]
        const da = replay.dir[i]
        const db = replay.dir[i + 1]
        handles.pivot.position.set(
          a[0] + (b[0] - a[0]) * f,
          a[1] + (b[1] - a[1]) * f,
          a[2] + (b[2] - a[2]) * f + HAND_HEIGHT_M,
        )
        direction.set(da[0] + (db[0] - da[0]) * f, da[1] + (db[1] - da[1]) * f, da[2] + (db[2] - da[2]) * f)
        if (direction.lengthSq() > 1e-9) {
          handles.pivot.quaternion.setFromUnitVectors(UP, direction.normalize())
        }

        const shown = Math.floor(index)
        const pre = Math.min(shown, replay.contact)
        const post = Math.max(0, shown - replay.contact)
        replay.sweetPre.geometry.instanceCount = pre
        replay.knobPre.geometry.instanceCount = pre
        replay.sweetPost.geometry.instanceCount = post
        replay.knobPost.geometry.instanceCount = post
        replay.sweetPost.visible = replay.knobPost.visible = post > 0
        replay.sweetPre.visible = replay.knobPre.visible = pre > 0
        replay.fan.geometry.setDrawRange(0, (Math.floor(pre / FAN_STRIDE) + 1) * 2)
        replay.ball.visible = replay.impact && index >= replay.contact
        handles.batMaterial.opacity = 1
      } else if (p.mode === "live") {
        const live = p.liveRef.current
        if (live && !live.imu_fault) {
          const d = rotateAboutVertical(batDirection(live.q, p.sensorFacesOut), -p.yawOffsetRef.current)
          direction.set(d[0], d[1], d[2])
          if (direction.lengthSq() > 1e-9) {
            handles.pivot.quaternion.setFromUnitVectors(UP, direction.normalize())
          }
          handles.batMaterial.opacity = 1
        } else {
          handles.batMaterial.opacity = 0.3
        }
        handles.pivot.position.set(0, 0, HAND_HEIGHT_M)
      }

      controls.update()
      renderer.render(scene, camera)
    }
    frame = requestAnimationFrame(render)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      controls.dispose()
      disposeReplay(handles)
      batMesh.geometry.dispose()
      puck.geometry.dispose()
      batMaterial.dispose()
      puckMaterial.dispose()
      grid.geometry.dispose()
      ;(grid.material as THREE.Material).dispose()
      arrow.geometry.dispose()
      ;(arrow.material as THREE.Material).dispose()
      renderer.dispose()
      renderer.domElement.remove()
      handlesRef.current = null
    }
  }, [])

  // Colours follow the theme tokens.
  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    handles.batMaterial.color.copy(cssColor("--scene-bat"))
    handles.puckMaterial.color.copy(cssColor("--ink-secondary"))
    ;(handles.grid.material as THREE.LineBasicMaterial).color.copy(cssColor("--scene-grid"))
    ;(handles.arrow.material as THREE.LineBasicMaterial).color.copy(cssColor("--ink-muted"))
    for (const m of handles.lineMaterials) m.color.copy(cssColor(m.userData.colorVar as string))
    const replay = handles.replay
    if (replay) {
      ;(replay.fan.material as THREE.LineBasicMaterial).color.copy(cssColor("--ink-muted"))
      ;(replay.ball.material as THREE.MeshStandardMaterial).color.copy(cssColor("--ink"))
    }
  }, [props.theme, props.swing])

  // The bat model follows the configured length (live) or the analysed swing's (replay).
  const shownLength = props.mode === "replay" && props.swing ? props.swing.bat.length_m : props.batLengthM
  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    handles.batMesh.geometry.dispose()
    handles.batMesh.geometry = batGeometry(shownLength)
  }, [shownLength])

  // Path objects for the selected swing.
  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    if (props.swing) {
      const size = new THREE.Vector2()
      handles.renderer.getSize(size)
      buildReplay(handles, props.swing, size)
      if (handles.replay) handles.replay.group.visible = propsRef.current.mode === "replay"
    } else {
      disposeReplay(handles)
    }
  }, [props.swing])

  // Each mode has its own framing; switching resets the camera.
  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    const replayMode = props.mode === "replay"
    if (handles.replay) handles.replay.group.visible = replayMode
    handles.arrow.visible = replayMode
    if (replayMode) {
      handles.controls.target.copy(toScene(-0.1, 0, 0.95))
      handles.camera.position.copy(toScene(1.2, -1.6, 1.5))
    } else {
      handles.controls.target.copy(toScene(0, 0, 1.3))
      handles.camera.position.copy(toScene(1.4, -1.6, 1.75))
    }
    handles.controls.update()
  }, [props.mode])

  return <div ref={containerRef} className="absolute inset-0 touch-none [&>canvas]:block [&>canvas]:size-full" />
}
