// Knob mount for the swing sensor.
//
// A rigid pod holds the carrier board and battery. A stretchy TPU sleeve holds
// the pod against the end of the knob: the sleeve is slightly smaller than the
// knob, so once it is pulled on, its tension presses the pod's three feet onto
// the knob's end face. Nothing is fixed to the bat. Roll the sleeve off to
// remove the sensor.
//
// One pod fits every bat. The sleeve is sized from the knob, so print one per
// knob size (change knob_d and knob_t, render "sleeve").
//
// Print the pod and lid in PETG or PLA, flange side down.
// Print the sleeve in TPU 95A, rim side down, no supports.
// Render one part at a time with `part` and export each to STL.

part = "assembly"; // [assembly, pod, lid, sleeve]

/* [Knob: sets the sleeve only] */
knob_d = 50;      // widest diameter of the knob
knob_t = 15;      // knob thickness along the bat
grip = 0.96;      // sleeve bore / knob diameter; lower grips harder
lip_ratio = 0.80; // sleeve's bottom opening / knob diameter

/* [Carrier board and battery: measure these before printing the pod] */
board_l = 37;     // carrier board length (USB-C end to switch end)
board_w = 28;
board_t = 1.6;
parts_h = 8;      // tallest part above the carrier board's top face
batt_l = 25;      // battery sits on the pod floor, between the board posts
batt_w = 20;
batt_t = 4;
usb_w = 13;       // opening for the USB-C plug, in the +X wall
usb_h = 7;
usb_z = 2.8;      // plug centre height above the carrier board's top face
switch_w = 9;     // opening for the slide switch, in the -X wall
switch_h = 5;
switch_z = 2.5;   // switch centre height above the carrier board's top face

/* [Shell] */
flange_d = 48;    // pod flange: the part held inside the sleeve
flange_t = 2.5;
foot_h = 1;
foot_d = 5;
rim_w = 3;        // sleeve rim overlap onto the flange
rim_t = 2;
sleeve_wall = 1.6;
pod_wall = 1.6;
floor_t = 1.2;
lid_t = 2;
post_d = 4.5;     // board posts, drilled for M2 self-tapping screws
post_inset = 2;   // post centre to board edge
boss_d = 5;       // lid screw bosses
pilot_d = 1.6;    // M2 self-tapping pilot hole
clear_d = 2.4;    // M2 clearance hole in the lid
fit = 0.2;
$fn = 96;

// Derived. z = 0 is the knob's end face; +z points away from the bat.
flange_r = flange_d / 2;
open_r = flange_r - rim_w;
grip_r = knob_d * grip / 2;
lip_r = knob_d * lip_ratio / 2;
lip_z = -(knob_t - 2);
foot_r = 0.3 * flange_d;
z_flange_top = foot_h + flange_t;
z_rim_top = z_flange_top + rim_t;
body_r = flange_r + sleeve_wall;
cav_r = body_r - pod_wall;
z_floor = z_rim_top + floor_t;
post_h = batt_t + 1.5;
z_board = z_floor + post_h;
cav_h = post_h + board_t + parts_h + 1;
z_top = z_floor + cav_h;
boss_r = cav_r - boss_d / 2 + 1;
post_clear = post_inset + post_d / 2;

assert(grip < 1, "grip must be below 1 or the sleeve will not hold");
assert(lip_r < grip_r, "lip_ratio must be smaller than grip");
assert(lip_z < -1, "knob_t is too small");
assert(open_r > 5, "rim_w is too large for the flange");
assert(sqrt(board_l * board_l + board_w * board_w) <= 2 * cav_r - 1,
       str("carrier board diagonal must be at most ", 2 * cav_r - 1, " mm"));
assert(board_w / 2 + 0.5 <= boss_r - boss_d / 2, "carrier board is too wide: it hits the lid bosses");
assert(batt_l <= board_l - 2 * post_clear || batt_w <= board_w - 2 * post_clear,
       "battery hits the board posts");
assert(sqrt(batt_l * batt_l + batt_w * batt_w) <= 2 * cav_r - 1, "battery does not fit in the pod");
assert(z_board + board_t + usb_z - usb_h / 2 >= z_floor, "USB opening reaches below the pod floor");
assert(z_board + board_t + usb_z + usb_h / 2 <= z_top, "USB opening reaches above the pod");
assert(z_board + board_t + switch_z - switch_h / 2 >= z_floor, "switch opening reaches below the pod floor");
assert(z_board + board_t + switch_z + switch_h / 2 <= z_top, "switch opening reaches above the pod");

module wall_slot(side, w, h, zc) {
    translate([side * (cav_r + pod_wall / 2), 0, z_board + board_t + zc])
        cube([pod_wall * 3, w, h], center = true);
}

module drilled_post(d, h) {
    difference() {
        cylinder(d = d, h = h);
        translate([0, 0, 1]) cylinder(d = pilot_d, h = h);
    }
}

module pod() {
    difference() {
        union() {
            for (a = [0:120:240]) rotate(a) translate([foot_r, 0, 0]) cylinder(d = foot_d, h = foot_h + 0.01);
            translate([0, 0, foot_h]) cylinder(r = flange_r, h = flange_t);
            translate([0, 0, z_flange_top - 0.01]) cylinder(r = open_r - fit, h = rim_t + 0.02);
            translate([0, 0, z_rim_top]) cylinder(r = body_r, h = z_top - z_rim_top);
        }
        translate([0, 0, z_floor]) cylinder(r = cav_r, h = cav_h + 1);
        wall_slot(1, usb_w, usb_h, usb_z);
        wall_slot(-1, switch_w, switch_h, switch_z);
    }
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (board_l / 2 - post_inset), sy * (board_w / 2 - post_inset), z_floor - 0.01])
            drilled_post(post_d, post_h + 0.01);
    for (sy = [-1, 1])
        translate([0, sy * boss_r, z_floor - 0.01]) drilled_post(boss_d, cav_h + 0.01);
}

module lid() {
    difference() {
        cylinder(r = body_r, h = lid_t);
        for (sy = [-1, 1]) translate([0, sy * boss_r, -1]) cylinder(d = clear_d, h = lid_t + 2);
    }
}

module sleeve() {
    outer0 = max(grip_r, flange_r) + sleeve_wall;
    // Rim and the band around the pod flange.
    rotate_extrude() polygon([
        [open_r, z_flange_top], [flange_r, z_flange_top], [flange_r, 0],
        [flange_r + sleeve_wall, 0], [flange_r + sleeve_wall, z_rim_top], [open_r, z_rim_top]]);
    // Tapered skirt: smaller than the knob, so it hooks under a flared or stepped knob.
    rotate_extrude() polygon([
        [grip_r, 0], [lip_r, lip_z], [lip_r + sleeve_wall, lip_z],
        [outer0, -sleeve_wall], [outer0, 0.01], [grip_r, 0.01]]);
}

if (part == "pod") pod();
else if (part == "lid") lid();
else if (part == "sleeve") sleeve();
else {
    color("SteelBlue") pod();
    color("SteelBlue") translate([0, 0, z_top + 0.5]) lid();
    color("DimGray", 0.6) sleeve();
    %translate([0, 0, -knob_t]) cylinder(d1 = knob_d * 0.55, d2 = knob_d, h = knob_t);
    echo(str("Pod height above the knob end: ", z_top + lid_t, " mm; outside diameter: ", 2 * body_r, " mm"));
}
