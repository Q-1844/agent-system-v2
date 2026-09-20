# src/generation/__init__.py
# Generation 边界包（S3C.1 起建）：provider-facing transformation。
#
# 🔴 职责边界（10.md/11.md）：Shot Binding 域（src/shot_binding/）只负责
#    canonical intent / validity / model-neutral pack compilation；
#    本包负责把 ShotReferencePack 转换成当前真实 provider 的输入
#    （reference media resolution / temporal filtering / prompt 组装 / race guard）。
#    provider-specific mapping 绝不反向进入 shot_binding 域。
from .image_adapter import (
    BoundGenerationError,
    BoundImageRequest,
    build_bound_first_frame_input,
    build_bound_last_frame_input,
    shot_has_binding,
    shot_has_visual_production_binding,
)
from .video_adapter import BoundVideoRequest, build_bound_video_input
from .request import GenerationRequest, ReferenceInput
from .agnes_adapter import (
    AgnesImageCall,
    AgnesRequestCompileError,
    AgnesVideoCall,
    compile_agnes_image_call,
    compile_agnes_video_call,
)
from .dialogue_control import (
    DialogueControlError,
    DialogueDirection,
    DialogueLineControl,
    NativeDialogueControl,
    append_dialogue_block,
    build_dialogue_block,
    classify_dialogue_body,
    compile_prompt_with_native_dialogue,
    resolve_dialogue_language,
    resolve_native_dialogue,
    split_dialogue_lines,
    split_speaker_prefix,
)

__all__ = [
    "AgnesImageCall",
    "AgnesRequestCompileError",
    "AgnesVideoCall",
    "BoundGenerationError",
    "BoundImageRequest",
    "BoundVideoRequest",
    "DialogueControlError",
    "DialogueDirection",
    "DialogueLineControl",
    "GenerationRequest",
    "NativeDialogueControl",
    "ReferenceInput",
    "append_dialogue_block",
    "build_bound_first_frame_input",
    "build_bound_last_frame_input",
    "build_bound_video_input",
    "build_dialogue_block",
    "classify_dialogue_body",
    "compile_agnes_image_call",
    "compile_agnes_video_call",
    "compile_prompt_with_native_dialogue",
    "resolve_dialogue_language",
    "resolve_native_dialogue",
    "shot_has_binding",
    "shot_has_visual_production_binding",
    "split_dialogue_lines",
    "split_speaker_prefix",
]
