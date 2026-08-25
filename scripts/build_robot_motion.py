"""Create a versioned Blender preview, then export an approved preview to BVH/CSV/MP4.

Run inside Blender. Preview and export are deliberately separate stages so the
developer files always come from the exact .blend file approved by the user.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import bpy
    from mathutils import Euler, Matrix, Quaternion, Vector
except ImportError as error:
    raise SystemExit("This script must run inside Blender. Use blender --background --python …") from error


@dataclass
class Joint:
    name: str
    parent_link: str
    child_link: str
    kind: str
    axis: tuple[float, float, float]
    xyz: tuple[float, float, float]
    rpy: tuple[float, float, float]
    lower: float | None
    upper: float | None


@dataclass
class LinkVisual:
    link_name: str
    filename: str
    xyz: tuple[float, float, float]
    rpy: tuple[float, float, float]
    scale: tuple[float, float, float]
    rgba: tuple[float, float, float, float]


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("preview", "export"), default="preview")
    parser.add_argument("--urdf", type=Path)
    parser.add_argument("--motion", type=Path)
    parser.add_argument("--blend", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--action-name", help="Descriptive output name used during preview")
    parser.add_argument("--ffmpeg", type=Path, help="Optional absolute path to an ffmpeg executable")
    parser.add_argument("--approved", action="store_true", help="Confirm the selected .blend was approved")
    args = parser.parse_args(argv)
    if args.stage == "preview" and (args.urdf is None or args.motion is None):
        parser.error("preview requires --urdf and --motion")
    if args.stage == "export" and (args.urdf is None or args.blend is None):
        parser.error("export requires --urdf and --blend")
    if args.stage == "export" and not args.approved:
        parser.error("export requires --approved after the user confirms the selected .blend")
    return args


def number_tuple(text: str | None, count: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if not text:
        return default
    values = tuple(float(value) for value in text.split())
    return values if len(values) == count else default


def parse_urdf(path: Path) -> list[Joint]:
    root = ET.parse(path).getroot()
    joints: list[Joint] = []
    for element in root.findall("joint"):
        kind = element.get("type", "fixed")
        if kind == "fixed":
            continue
        parent = element.find("parent")
        child = element.find("child")
        if parent is None or child is None:
            continue
        origin = element.find("origin")
        axis = element.find("axis")
        limit = element.find("limit")
        lower = float(limit.get("lower")) if limit is not None and limit.get("lower") else None
        upper = float(limit.get("upper")) if limit is not None and limit.get("upper") else None
        joints.append(Joint(
            name=element.get("name", "unnamed_joint"),
            parent_link=parent.get("link", ""),
            child_link=child.get("link", ""),
            kind=kind,
            axis=number_tuple(axis.get("xyz") if axis is not None else None, 3, (0.0, 0.0, 1.0)),
            xyz=number_tuple(origin.get("xyz") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
            rpy=number_tuple(origin.get("rpy") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
            lower=lower,
            upper=upper,
        ))
    if not joints:
        raise ValueError("URDF 中没有可动关节（revolute / continuous / prismatic）。")
    return joints


def parse_link_visuals(path: Path) -> tuple[list[str], list[LinkVisual]]:
    root = ET.parse(path).getroot()
    link_names: list[str] = []
    visuals: list[LinkVisual] = []
    for link in root.findall("link"):
        link_name = link.get("name", "unnamed_link")
        link_names.append(link_name)
        for visual in link.findall("visual"):
            mesh = visual.find("geometry/mesh")
            if mesh is None or not mesh.get("filename"):
                continue
            origin = visual.find("origin")
            color = visual.find("material/color")
            visuals.append(LinkVisual(
                link_name=link_name,
                filename=mesh.get("filename", ""),
                xyz=number_tuple(origin.get("xyz") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
                rpy=number_tuple(origin.get("rpy") if origin is not None else None, 3, (0.0, 0.0, 0.0)),
                scale=number_tuple(mesh.get("scale"), 3, (1.0, 1.0, 1.0)),
                rgba=number_tuple(color.get("rgba") if color is not None else None, 4, (0.72, 0.74, 0.78, 1.0)),
            ))
    if not link_names:
        raise ValueError("URDF 中没有 link。")
    if not visuals:
        raise ValueError("URDF 中没有可用于 Blender 预览的 mesh visual；停止生成占位骨架。")
    return link_names, visuals


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def resolve_mesh_path(urdf: Path, filename: str) -> Path:
    project_root = urdf.parent.parent
    candidates: list[Path] = []
    if filename.startswith("package://"):
        package_relative = Path(filename[len("package://") :])
        parts = package_relative.parts
        if len(parts) > 1:
            candidates.append(project_root.joinpath(*parts[1:]))
        candidates.append(project_root / package_relative)
        candidates.append(project_root / "meshes" / package_relative.name)
    elif filename.startswith("file://"):
        candidates.append(Path(filename[len("file://") :]))
    else:
        source = Path(filename)
        candidates.append(source if source.is_absolute() else urdf.parent / source)
        candidates.append(project_root / source)
        candidates.append(project_root / "meshes" / source.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"找不到 URDF visual 网格：{filename}")


def import_stl(path: Path) -> list[Any]:
    bpy.ops.object.select_all(action="DESELECT")
    try:
        bpy.ops.wm.stl_import(filepath=str(path))
    except (AttributeError, RuntimeError, TypeError):
        bpy.ops.import_mesh.stl(filepath=str(path))
    imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not imported:
        raise RuntimeError(f"Blender 未能导入 STL：{path}")
    return imported


def create_visual_robot(urdf: Path, joints: list[Joint]) -> tuple[dict[str, Any], int]:
    link_names, visuals = parse_link_visuals(urdf)
    link_objects: dict[str, Any] = {}
    for link_name in link_names:
        empty = bpy.data.objects.new(f"link::{link_name}", None)
        empty.empty_display_type = "PLAIN_AXES"
        empty.empty_display_size = 0.006
        bpy.context.collection.objects.link(empty)
        link_objects[link_name] = empty

    visual_by_joint: dict[str, Any] = {}
    for joint in joints:
        parent = link_objects.get(joint.parent_link)
        child = link_objects.get(joint.child_link)
        if parent is None or child is None:
            raise ValueError(f"关节 {joint.name} 引用了不存在的 link。")
        child.parent = parent
        child.matrix_parent_inverse = Matrix.Identity(4)
        child.location = joint.xyz
        child.rotation_mode = "QUATERNION"
        child.rotation_quaternion = Euler(joint.rpy, "XYZ").to_quaternion()
        child["urdf_joint_name"] = joint.name
        visual_by_joint[joint.name] = child

    material_cache: dict[tuple[float, float, float, float], Any] = {}
    mesh_count = 0
    for index, visual in enumerate(visuals, start=1):
        mesh_path = resolve_mesh_path(urdf, visual.filename)
        for imported in import_stl(mesh_path):
            imported.name = f"visual::{visual.link_name}::{index:02d}"
            imported.parent = link_objects[visual.link_name]
            imported.matrix_parent_inverse = Matrix.Identity(4)
            imported.location = visual.xyz
            imported.rotation_mode = "XYZ"
            imported.rotation_euler = visual.rpy
            imported.scale = visual.scale
            imported["source_mesh"] = str(mesh_path)
            material = material_cache.get(visual.rgba)
            if material is None:
                material = bpy.data.materials.new(name=f"URDF_Material_{len(material_cache) + 1:02d}")
                material.diffuse_color = visual.rgba
                material_cache[visual.rgba] = material
            if imported.data.materials:
                imported.data.materials[0] = material
            else:
                imported.data.materials.append(material)
            mesh_count += 1
    if mesh_count != len(visuals):
        raise RuntimeError(f"URDF visual 数量 {len(visuals)}，实际导入网格 {mesh_count}；停止生成不完整预览。")
    return visual_by_joint, mesh_count


def create_armature(joints: list[Joint]) -> Any:
    arm_data = bpy.data.armatures.new("URDF_Robot_Rig")
    arm = bpy.data.objects.new("URDF_Robot_Rig", arm_data)
    bpy.context.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    arm.show_in_front = False
    arm_data.display_type = "STICK"
    bpy.ops.object.mode_set(mode="EDIT")

    child_to_joint = {joint.child_link: joint for joint in joints}
    roots = [joint for joint in joints if joint.parent_link not in child_to_joint]

    def add_branch(joint: Joint, parent_bone: Any | None, parent_matrix: Matrix) -> None:
        joint_matrix = (
            parent_matrix
            @ Matrix.Translation(Vector(joint.xyz))
            @ Euler(joint.rpy, "XYZ").to_matrix().to_4x4()
        )
        head = joint_matrix.translation
        direction = joint_matrix.to_3x3() @ Vector(joint.axis)
        if direction.length < 0.001:
            direction = Vector((0.0, 0.0, 1.0))
        direction.normalize()
        bone = arm_data.edit_bones.new(joint.name)
        bone.head = head
        bone.tail = head + direction * 0.08
        bone.parent = parent_bone
        bone.use_connect = False
        for next_joint in (item for item in joints if item.parent_link == joint.child_link):
            add_branch(next_joint, bone, joint_matrix)

    for root in roots:
        add_branch(root, None, Matrix.Identity(4))
    bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in arm.pose.bones:
        pose_bone.rotation_mode = "QUATERNION"
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.hide_set(True)
    arm.hide_render = True
    return arm


def clamp_degrees(angle: float, joint: Joint) -> tuple[float, bool]:
    if joint.kind == "continuous" or joint.lower is None or joint.upper is None:
        return angle, False
    radians = math.radians(angle)
    bounded = min(max(radians, joint.lower), joint.upper)
    return math.degrees(bounded), abs(bounded - radians) > 1e-9


def property_name(joint_name: str) -> str:
    return f"joint_angle_deg::{joint_name}"


def apply_motion(
    arm: Any,
    visual_by_joint: dict[str, Any],
    joints: list[Joint],
    motion: dict[str, Any],
) -> tuple[int, int, list[dict[str, Any]]]:
    fps = int(motion.get("fps", 30))
    duration = float(motion.get("duration_seconds", 4))
    if fps <= 0 or duration <= 0:
        raise ValueError("fps 和 duration_seconds 必须大于 0。")
    final_frame = round(fps * duration) + 1
    frames = motion.get("keyframes")
    if not isinstance(frames, list) or not frames:
        raise ValueError("motion JSON 必须有至少一条 keyframes。")
    lookup = {joint.name: joint for joint in joints}
    warnings: list[dict[str, Any]] = []
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.frame_start = 1
    scene.frame_end = final_frame
    for joint in joints:
        arm[property_name(joint.name)] = 0.0
    for item in sorted(frames, key=lambda entry: int(entry["frame"])):
        frame = int(item["frame"])
        if not 1 <= frame <= final_frame:
            raise ValueError(f"关键帧 {frame} 不在 1..{final_frame} 内。")
        pose = item.get("pose", {})
        for joint_name, degrees in pose.items():
            if joint_name not in lookup:
                warnings.append({"type": "unknown_joint", "joint": joint_name, "frame": frame})
                continue
            joint = lookup[joint_name]
            value, clipped = clamp_degrees(float(degrees), joint)
            if clipped:
                warnings.append({
                    "type": "limit_clamped", "joint": joint_name, "frame": frame,
                    "requested_deg": degrees, "previewed_deg": value,
                })
            arm[property_name(joint_name)] = value
            arm.keyframe_insert(data_path=f'["{property_name(joint_name)}"]', frame=frame)
            link_object = visual_by_joint.get(joint_name)
            if link_object is not None:
                origin_rotation = Euler(joint.rpy, "XYZ").to_quaternion()
                joint_rotation = Quaternion(Vector(joint.axis).normalized(), math.radians(value))
                link_object.rotation_quaternion = origin_rotation @ joint_rotation
                link_object.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            bone = arm.pose.bones.get(joint_name)
            if bone is not None:
                # Every edit bone's local +Y is aligned to the URDF joint axis.
                bone.rotation_quaternion = Quaternion((0.0, 1.0, 0.0), math.radians(value))
                bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
    mode = motion.get("interpolation", "BEZIER").upper()
    interpolation = mode if mode in {"BEZIER", "LINEAR", "CONSTANT"} else "BEZIER"
    for animated_object in [arm, *visual_by_joint.values()]:
        action = animated_object.animation_data.action if animated_object.animation_data else None
        if action and hasattr(action, "fcurves"):
            for curve in action.fcurves:
                for point in curve.keyframe_points:
                    point.interpolation = interpolation
    scene.frame_set(1)
    return fps, final_frame, warnings


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z._\u4e00-\u9fff-]+", "_", value.strip()).strip("._")
    return stem or "robot_motion"


def unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{index:03d}{suffix}"
        index += 1
    return candidate


def unique_directory(directory: Path, stem: str) -> Path:
    candidate = directory / stem
    index = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{index:03d}"
        index += 1
    return candidate


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def joint_angle_degrees(arm: Any, joint: Joint) -> float:
    key = property_name(joint.name)
    if key not in arm:
        raise ValueError(f"Blender 文件缺少可审计角度轨道：{joint.name}")
    return float(arm[key])


def validate_limits(arm: Any, joints: list[Joint], final_frame: int) -> None:
    for frame in range(1, final_frame + 1):
        bpy.context.scene.frame_set(frame)
        for joint in joints:
            if joint.kind == "continuous" or joint.lower is None or joint.upper is None:
                continue
            radians = math.radians(joint_angle_degrees(arm, joint))
            if radians < joint.lower - 1e-8 or radians > joint.upper + 1e-8:
                raise ValueError(f"第 {frame} 帧关节 {joint.name} 超出 URDF 限位，停止导出。")


def export_csv(arm: Any, joints: list[Joint], fps: int, final_frame: int, destination: Path) -> None:
    names = [joint.name for joint in joints]
    header = ["frame", "time_seconds"] + [f"{name}_rad" for name in names] + [f"{name}_deg" for name in names]
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for frame in range(1, final_frame + 1):
            bpy.context.scene.frame_set(frame)
            degrees = [joint_angle_degrees(arm, joint) for joint in joints]
            radians = [math.radians(value) for value in degrees]
            writer.writerow(
                [frame, f"{(frame - 1) / fps:.6f}"]
                + [f"{value:.8f}" for value in radians]
                + [f"{value:.6f}" for value in degrees]
            )


def export_bvh(arm: Any, destination: Path, fps: int) -> None:
    arm.hide_set(False)
    arm.hide_viewport = False
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    options = {
        "filepath": str(destination),
        "check_existing": False,
        "frame_start": bpy.context.scene.frame_start,
        "frame_end": bpy.context.scene.frame_end,
        "frame_time": 1.0 / fps,
        "root_transform_only": False,
    }
    try:
        bpy.ops.export_anim.bvh(**options)
    except (AttributeError, RuntimeError, TypeError):
        options.pop("frame_time", None)
        options.pop("root_transform_only", None)
        bpy.ops.export_anim.bvh(**options)


def scene_motion_bounds(final_frame: int) -> tuple[Vector, float]:
    """Return an animation-wide center and radius for automatic camera framing."""
    scene = bpy.context.scene
    points: list[Vector] = []
    for frame in range(scene.frame_start, final_frame + 1):
        scene.frame_set(frame)
        for obj in bpy.data.objects:
            if obj.type != "MESH" or obj.hide_render:
                continue
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        raise RuntimeError("场景中没有可渲染的机器人网格，无法生成动作预览视频。")
    minimum = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
    maximum = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
    center = (minimum + maximum) * 0.5
    radius = max((maximum - minimum).length * 0.5, 0.2)
    scene.frame_set(scene.frame_start)
    return center, radius


def aim_at(obj: Any, target: Vector) -> None:
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area_light(name: str, location: Vector, target: Vector, energy: float, size: float) -> None:
    data = bpy.data.lights.new(name=name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    light = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(light)
    light.location = location
    aim_at(light, target)


def find_ffmpeg(explicit: Path | None) -> tuple[Path, str, str]:
    playwright_encoders = sorted((Path.home() / "Library/Caches/ms-playwright").glob("ffmpeg-*/ffmpeg-mac"), reverse=True)
    candidates = [
        explicit,
        Path(shutil.which("ffmpeg")) if shutil.which("ffmpeg") else None,
        Path("/opt/homebrew/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
        Path("/Applications/VideoFusion-macOS.app/Contents/Resources/ffmpeg"),
        *playwright_encoders,
    ]
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        probe = subprocess.run(
            [str(candidate), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
        encoders = f"{probe.stdout}\n{probe.stderr}"
        if probe.returncode == 0 and "libx264" in encoders:
            return candidate.resolve(), "libx264", ".mp4"
        if probe.returncode == 0 and "h264_videotoolbox" in encoders:
            return candidate.resolve(), "h264_videotoolbox", ".mp4"
        if probe.returncode == 0 and "libvpx" in encoders:
            return candidate.resolve(), "libvpx", ".webm"
    raise RuntimeError("找不到可运行且支持 H.264 或 VP8 的 ffmpeg，无法编码预览视频；请安装 ffmpeg 或传入 --ffmpeg 绝对路径。")


def render_preview_video(destination: Path, final_frame: int, explicit_ffmpeg: Path | None) -> tuple[int, int, Path]:
    scene = bpy.context.scene
    ffmpeg, video_codec, video_suffix = find_ffmpeg(explicit_ffmpeg)
    destination = destination.with_suffix(video_suffix)
    center, radius = scene_motion_bounds(final_frame)

    camera_data = bpy.data.cameras.new("Delivery_Preview_Camera")
    camera_data.lens = 52
    camera = bpy.data.objects.new("Delivery_Preview_Camera", camera_data)
    bpy.context.collection.objects.link(camera)
    view_direction = Vector((1.35, -1.8, 0.55)).normalized()
    camera.location = center + view_direction * radius * 3.2
    aim_at(camera, center)
    scene.camera = camera

    add_area_light("Delivery_Key", camera.location + Vector((-radius, 0.0, radius)), center, 1100.0, radius * 2.0)
    add_area_light("Delivery_Fill", center + Vector((-radius * 2.0, radius * 1.5, radius)), center, 700.0, radius * 2.5)
    add_area_light("Delivery_Rim", center + Vector((0.0, radius, radius * 2.5)), center, 900.0, radius * 1.8)
    if scene.world is None:
        scene.world = bpy.data.worlds.new("Delivery_Preview_World")
    scene.world.color = (0.035, 0.035, 0.045)

    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 92
    scene.frame_start = 1
    scene.frame_end = final_frame
    scene.frame_set(1)
    frames_dir = Path(tempfile.mkdtemp(prefix=".blender-preview-frames-", dir=destination.parent))
    try:
        scene.render.filepath = str(frames_dir / "frame_")
        bpy.ops.render.render(animation=True)
        codec_options = (
            ["-c:v", video_codec, "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            if video_suffix == ".mp4"
            else ["-c:v", video_codec, "-pix_fmt", "yuv420p", "-b:v", "2M"]
        )
        encoder = subprocess.Popen(
            [
                str(ffmpeg), "-y", "-loglevel", "error",
                "-f", "image2pipe", "-framerate", str(scene.render.fps),
                "-vcodec", "mjpeg", "-i", "pipe:0",
                *codec_options, str(destination),
            ],
            stdin=subprocess.PIPE,
        )
        if encoder.stdin is None:
            raise RuntimeError("无法打开 ffmpeg 图片输入管道。")
        try:
            for frame in range(scene.frame_start, scene.frame_end + 1):
                frame_path = frames_dir / f"frame_{frame:04d}.jpg"
                if not frame_path.is_file():
                    raise RuntimeError(f"Blender 缺少视频帧：{frame_path.name}")
                with frame_path.open("rb") as handle:
                    shutil.copyfileobj(handle, encoder.stdin)
        finally:
            encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError("ffmpeg 无法把 Blender 帧编码为动作预览视频。")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Blender 预览帧未能编码为有效的 MP4 动作预览视频。")
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
    return scene.render.resolution_x, scene.render.resolution_y, destination


def find_armature() -> Any:
    arm = bpy.data.objects.get("URDF_Robot_Rig")
    if arm is None or arm.type != "ARMATURE":
        arm = next((obj for obj in bpy.data.objects if obj.type == "ARMATURE"), None)
    if arm is None:
        raise ValueError("已确认的 Blender 文件中找不到机器人骨架。")
    return arm


def run_preview(args: argparse.Namespace) -> dict[str, Any]:
    if not args.urdf.is_file() or not args.motion.is_file():
        raise FileNotFoundError("找不到 URDF 或 motion JSON 文件。请使用绝对路径。")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    joints = parse_urdf(args.urdf)
    motion = json.loads(args.motion.read_text(encoding="utf-8"))
    clear_scene()
    arm = create_armature(joints)
    visual_by_joint, mesh_count = create_visual_robot(args.urdf, joints)
    fps, final_frame, warnings = apply_motion(arm, visual_by_joint, joints, motion)
    bpy.ops.object.select_all(action="DESELECT")
    stem = safe_stem(args.action_name or args.motion.stem)
    blend = unique_path(args.output_dir, stem, ".blend")
    stem = blend.stem
    motion_copy = args.output_dir / f"{stem}_motion.json"
    report_path = args.output_dir / f"{stem}_preview_report.json"
    motion_copy.write_text(json.dumps(motion, ensure_ascii=False, indent=2), encoding="utf-8")
    arm["source_urdf"] = str(args.urdf.resolve())
    arm["source_motion"] = str(motion_copy.resolve())
    arm["preview_approval_required"] = True
    bpy.context.scene["motion_stage"] = "preview"
    bpy.context.scene["approval_status"] = "pending"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    report = {
        "stage": "preview",
        "approval_required": True,
        "urdf": str(args.urdf.resolve()),
        "fps": fps,
        "duration_seconds": (final_frame - 1) / fps,
        "frame_count": final_frame,
        "joint_count": len(joints),
        "visual_mesh_count": mesh_count,
        "armature_hidden_in_preview": True,
        "outputs": {"blend": str(blend.resolve()), "motion_plan": str(motion_copy.resolve())},
        "warnings": warnings,
        "next_step": "Open and play the .blend file. Export BVH/CSV only after explicit user approval of this exact file.",
    }
    report["outputs"]["preview_report"] = str(report_path.resolve())
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def run_export(args: argparse.Namespace) -> dict[str, Any]:
    if not args.urdf.is_file() or not args.blend.is_file():
        raise FileNotFoundError("找不到 URDF 或已确认的 Blender 文件。请使用绝对路径。")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))
    arm = find_armature()
    joints = parse_urdf(args.urdf)
    scene = bpy.context.scene
    fps = int(scene.render.fps)
    final_frame = int(scene.frame_end)
    validate_limits(arm, joints, final_frame)
    base_stem = safe_stem(args.blend.stem)
    delivery_dir = unique_directory(args.output_dir, f"{base_stem}_delivery")
    temp_root = Path(tempfile.mkdtemp(prefix=".urdf-motion-delivery-", dir=args.output_dir))
    try:
        packaged_blend = temp_root / f"{base_stem}.blend"
        bvh = temp_root / f"{base_stem}.bvh"
        csv_path = temp_root / f"{base_stem}_joint_trajectory.csv"
        video_path = temp_root / f"{base_stem}_preview.mp4"
        report_path = temp_root / f"{base_stem}_export_report.json"
        shutil.copy2(args.blend, packaged_blend)
        export_bvh(arm, bvh, fps)
        export_csv(arm, joints, fps, final_frame, csv_path)
        video_width, video_height, video_path = render_preview_video(video_path, final_frame, args.ffmpeg)
        if not packaged_blend.is_file() or not bvh.is_file() or not csv_path.is_file() or not video_path.is_file():
            raise RuntimeError("开发交付包临时生成不完整，未发布文件夹。")
        csv_rows = sum(1 for _ in csv_path.open("r", encoding="utf-8")) - 1
        if csv_rows != final_frame:
            raise RuntimeError(f"CSV 数据行数 {csv_rows} 与帧数 {final_frame} 不一致，未发布文件夹。")

        final_blend = delivery_dir / packaged_blend.name
        final_bvh = delivery_dir / bvh.name
        final_csv = delivery_dir / csv_path.name
        final_video = delivery_dir / video_path.name
        final_report = delivery_dir / report_path.name
        report = {
            "stage": "approved_export",
            "approved_source_blend": str(args.blend.resolve()),
            "packaged_blend": str(final_blend.resolve()),
            "source_blend_sha256": sha256(args.blend),
            "urdf": str(args.urdf.resolve()),
            "fps": fps,
            "duration_seconds": (final_frame - 1) / fps,
            "frame_count": final_frame,
            "csv_data_rows": final_frame,
            "video_resolution": [video_width, video_height],
            "video_format": "MP4/H.264" if video_path.suffix == ".mp4" else "WebM/VP8",
            "joint_count": len(joints),
            "joint_order": [joint.name for joint in joints],
            "csv_layout": "one row per frame; radians columns first, then degrees columns",
            "outputs": {
                "delivery_folder": str(delivery_dir.resolve()),
                "blend": str(final_blend.resolve()),
                "bvh": str(final_bvh.resolve()),
                "csv": str(final_csv.resolve()),
                "preview_video": str(final_video.resolve()),
                "export_report": str(final_report.resolve()),
            },
            "safety_notice": "Developer integration data only. Validate mapping, zero offsets, velocity, acceleration, torque, balance and collision before hardware use.",
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if len([path for path in temp_root.iterdir() if path.is_file()]) != 5:
            raise RuntimeError("开发交付文件夹必须且只能包含 5 个文件。")
        temp_root.replace(delivery_dir)
        return report
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    args = parse_args()
    report = run_preview(args) if args.stage == "preview" else run_export(args)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
