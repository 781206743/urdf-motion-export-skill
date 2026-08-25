# Robot motion export: format and safety guide

## Two-stage delivery gate

Always create a new, versioned `.blend` preview first. Do not create BVH or CSV
at this stage. Export only after the user explicitly approves the exact Blender
file; use that approved file as the export source so preview and delivery cannot
silently diverge.

When the motion changes, create another `.blend` version instead of overwriting
the previously reviewed file.

## What the outputs mean

| File | Simple meaning | Good for | Not sufficient for |
|---|---|---|---|
| `.blend` | Blender project containing the keyed robot rig | Preview and editing | Physics validation |
| `.bvh` | A common file for a moving skeleton | Animation exchange | Robot controller input |
| `.csv` | A spreadsheet-like table of every joint angle in every frame | Inspection and conversion | Motor command stream |

The CSV has one row per frame. It lists radians first (common in ROS and MuJoCo),
then degrees (easy for people to inspect). Time starts from zero at frame 1. The
export report records joint order, frame rate, frame count, and the SHA-256 hash
of the approved source `.blend`.

## Mapping natural language to a safe draft

1. Name a pose before and after each movement.
2. Keep a hold as two identical keyframes: for a two-second hold at 30 fps, leave 60 frames between them.
3. Use `BEZIER` for natural, soft motion. Use `LINEAR` only for intentionally mechanical motions.
4. Use only the joint names discovered from the URDF. A left/right mirror can have opposite signs, so inspect the Blender preview.
5. Let URDF limits clip excess angles. A clip warning means the requested pose could not be faithfully made.

## Before an animation becomes a robot command

Think of BVH/CSV as choreography notes. The robot controller needs safe conductor notes too:

- correct robot-specific joint order, sign and zero offset;
- position, velocity, acceleration and torque limits;
- left/right foot contact and center-of-mass balance;
- self-collision and ground-collision checks;
- a harness or emergency stop for first hardware testing.

First replay the CSV in MuJoCo at a slow speed. A successful Blender preview only means the skeleton looks plausible; it does not prove that the robot can keep its balance.
