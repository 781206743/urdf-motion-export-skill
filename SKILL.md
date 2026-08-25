---
name: urdf-motion-export-skill
description: >-
  Convert a URDF plus a Chinese or English motion description into a new,
  versioned Blender animation for user preview, then export the exact approved
  .blend animation to BVH, frame-by-frame CSV, and a Blender preview video,
  packaged together in a separate versioned delivery folder. Use for URDF
  animation, robot dance, 加油动作, 打气动作, Blender robot motion, BVH export, CSV
  joint trajectory, preview video, or natural-language robot choreography that
  requires a visual approval gate.
---

# URDF 动作预览与确认后导出

把用户提供的 URDF 和动作描述转换为可在 Blender 播放的机器人动作。强制分为预览和交付两个阶段。

## 强制阶段门

### 阶段一：创建 Blender 预览

1. 解析 URDF，读取真实关节名、父子关系、旋转轴和限位。
2. 把动作描述转换为关键帧 JSON。默认不动腿；除非用户明确要求腿部动作。
3. 导入 URDF 中每个 link 的真实 visual 网格（STL/mesh），按 joint 层级装配并驱动；不得把 B-Bone、方块或其他占位骨架当成机器人外观。
4. 使用 `scripts/build_robot_motion.py --stage preview` 创建一个**新的、自动版本化的 `.blend` 文件**并写入动画。把 BVH 导出骨架隐藏，只显示真实机器人网格。
5. 每次首次生成或动作修改都创建新 `.blend`，不得覆盖旧预览。
6. 核对预览报告中的 `visual_mesh_count` 等于 URDF mesh visual 数；缺少任何网格时停止，不交付残缺预览。
7. 只交付 `.blend`、动作 JSON 和预览报告；不得在此阶段生成 BVH 或 CSV。
8. 请用户打开 `.blend`、播放时间线并明确确认具体文件。未经确认，停止在阶段一。

预览阶段不再要求用户先确认文字关键帧；`.blend` 动画本身是效果确认依据。若用户明确只想先看计划，则先展示计划，再创建预览。

### 阶段二：导出已确认动画

仅当用户明确表示“效果确认”“可以导出”或等价意思，并能定位已确认的 `.blend` 时执行：

1. 使用 `--stage export --blend <已确认文件>` 从该 `.blend` 原样导出，不重新生成动作。
2. 再次检查所有逐帧角度均在 URDF 限位内；超限则停止导出并报告。
3. 每次导出创建独立、自动版本化的 `<动作名>_delivery/` 文件夹，不得把交付文件散放在输出根目录，也不得覆盖旧文件夹。
4. 从已确认 `.blend` 的同一时间线渲染一段动作预览视频。优先输出 H.264 MP4；编码器不支持 H.264 时回退为 VP8 WebM。视频必须显示真实机器人网格，自动配置可看清全身动作的相机、灯光和背景；不得以静态截图或占位骨架代替。
5. 在交付文件夹中固定放且只放 5 个文件：已确认 `.blend` 的副本、BVH、逐帧 CSV、一段动作预览视频、导出报告。动作 JSON 和预览报告保留在外层，不混入开发交付包。
6. CSV 为一帧一行，先列弧度、再列角度；报告固定关节顺序、帧率、帧数、视频分辨率、源 `.blend` 哈希以及交付文件夹路径。
7. 检查 CSV 数据行数等于 `duration × fps + 1`，检查 MP4 存在且非空，并检查交付文件夹恰好有 5 个文件。

即使用户要求“直接生成”，也只能直接生成 Blender 预览；BVH/CSV 仍需对具体预览文件的明确确认。

## 动作计划规则

- `joint` 必须是 URDF 中真实的关节名，角度单位为度。
- 同一帧未出现的关节保持上一姿势。
- 左右关节的正负方向必须依据 URDF `axis` 和 Blender 预览，不得想当然镜像。
- 关键帧必须按 URDF 限位裁剪；无位置限位时标记“未验证”并采用保守角度。
- 使用 `BEZIER` 表达自然柔和动作，仅在明确需要机械节奏时使用 `LINEAR`。
- 用两个相同姿势关键帧表达停顿。

```json
{
  "fps": 30,
  "duration_seconds": 4,
  "interpolation": "BEZIER",
  "keyframes": [
    {"frame": 1, "pose": {"head_pitch_joint": 0}},
    {"frame": 31, "pose": {"right_shoulder_pitch_joint": -65}},
    {"frame": 46, "pose": {"head_pitch_joint": 14}},
    {"frame": 61, "pose": {"right_shoulder_pitch_joint": 20, "head_pitch_joint": 0}},
    {"frame": 121, "pose": {"right_shoulder_pitch_joint": 0}}
  ]
}
```

## 执行命令

先定位 Blender。macOS 常见路径为 `/Applications/Blender.app/Contents/MacOS/Blender`；找不到时清楚说明，不伪造产物。

创建新的预览文件：

```bash
blender --background --python scripts/build_robot_motion.py -- \
  --stage preview \
  --urdf /absolute/path/robot.urdf \
  --motion /absolute/path/motion.json \
  --output-dir /absolute/path/output \
  --action-name heartfelt_thanks
```

用户确认指定 `.blend` 后再导出：

```bash
blender --background --python scripts/build_robot_motion.py -- \
  --stage export \
  --approved \
  --urdf /absolute/path/robot.urdf \
  --blend /absolute/path/output/heartfelt_thanks.blend \
  --output-dir /absolute/path/output
```

视频编码优先使用 PATH 中的 `ffmpeg`；找不到时可在导出命令末尾增加 `--ffmpeg /absolute/path/ffmpeg`。没有可用编码器时停止交付，不生成缺少视频的不完整文件夹。

## 输出与安全边界

- 预览阶段：版本化 `.blend`、`*_motion.json`、`*_preview_report.json`。
- 确认后：创建 `<动作名>_delivery/`；其中固定包含 `<动作名>.blend`、`<动作名>.bvh`、`<动作名>_joint_trajectory.csv`、`<动作名>_preview.mp4`（或回退的 `.webm`）、`<动作名>_export_report.json`。重复导出自动使用 `_002`、`_003` 文件夹。

BVH/CSV 可交付开发做动作集成，但不是可绕过适配和安全验证的实体机器人电机指令。用于真机前必须重新核对关节顺序、符号、单位、零位、速度、加速度、力矩、平衡、自碰撞和急停，并先在 MuJoCo 或等效仿真中回放。

需要解释格式或执行真机前检查时，读取 [references/robot-motion-safety.md](references/robot-motion-safety.md)。
