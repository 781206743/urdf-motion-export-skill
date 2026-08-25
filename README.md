# URDF Motion Export Skill

Give the agent a robot URDF and a motion description. It first creates a new,
versioned Blender animation for review. After the user approves that exact
`.blend`, it creates a versioned delivery folder containing exactly four files:
the approved Blender file, BVH, frame-by-frame CSV, and an audit report.
The Blender preview imports the URDF's real visual meshes and animates them through
the link/joint hierarchy; the BVH armature stays hidden during visual review.

## Requirements

- Blender installed and available as `blender`, or invoked via its absolute executable path.
- A URDF with named movable joints.
- A motion JSON plan produced from the user's motion description.

## Use in Codex

Place this folder in `~/.codex/skills/`, then start a new task and write `/urdf-motion-export-skill` followed by the URDF path and the desired action.

## Install elsewhere

Run `./install.sh --platform universal`, or choose `claude-code`, `cursor`, `copilot`, `codex`, and other supported platforms.

## Important

Preview and export are separate stages. BVH/CSV must not be created before the
user approves the Blender animation. The exports are integration data, not
certified motor commands; validate them in simulation before physical use.
