"""剧本与角色数据模型 — 与 PLATFORM_SPEC.md §4.8 关键数据结构对齐。"""

from __future__ import annotations

from enum import Enum
import time
from typing import Any

from pydantic import BaseModel, Field


class ShotType(str, Enum):
    CLOSEUP = "特写"
    BUST = "近景"
    MEDIUM = "中景"
    WIDE = "远景"


class Emotion(str, Enum):
    TENSION = "tension"
    ROMANTIC = "romantic"
    HAPPY = "happy"
    SAD = "sad"
    MYSTERIOUS = "mysterious"


class CameraMovement(str, Enum):
    STATIC = "static"
    PAN = "pan"
    TILT = "tilt"
    ZOOM = "zoom"


class Character(BaseModel):
    character_id: str
    name: str
    role: str = ""
    age: int | None = None
    description: str = ""
    personality: str = ""
    voice_id: str = ""
    reference_views: list[str] = Field(default_factory=list)


class Scene(BaseModel):
    scene_id: int
    episode: int = 1
    shot_type: str = "中景"
    description: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    character_actions: str = ""
    dialogue: str = ""
    emotion: str = "neutral"
    duration_seconds: int = 5
    camera_movement: str = "static"
    narrative_beat: str = Field(
        "",
        description="叙事节拍：hook(强钩子)/escalation(冲突升级)/reversal(反转)/cliffhanger(悬念)/emotional_beat(情绪落点)/transition(过渡)",
    )


class Script(BaseModel):
    """剧本 JSON 结构（对应 PLATFORM_SPEC §4.8）。"""

    project_id: str = ""
    title: str = ""
    genre: str = ""
    aspect_ratio: str = "9:16"
    total_episodes: int = 1
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


class ScriptRequest(BaseModel):
    """剧本 Agent 输入。"""

    premise: str = Field(..., description="一句话创意")
    genre: str = Field("都市悬疑", description="题材")
    style: str = Field("写实电影感", description="画风（M15.1：全链路画风锚定，场景 prompt 与角色/分镜统一风格）")
    episodes: int = Field(1, ge=1, le=100, description="集数")
    scenes_per_episode: int = Field(5, ge=1, le=30, description="每集分镜数")
    monetization_mode: str = Field(
        "iaa",
        description="变现模式：iaa(免费剧/红果模式，每集末尾钩子驱动完播) / iap(付费剧，第8-12集设首充卡点)",
    )
    web_search: bool = Field(
        False,
        description="是否联网搜索同题材参考资料（默认关闭；也可由 SCRIPT_WEB_SEARCH_ENABLED 全局打开）",
    )


class CharacterAsset(BaseModel):
    """角色资产库条目：跨集/跨镜一致性的外观锁定卡（本地 JSON 持久化）。"""

    character_id: str
    name: str
    role: str = ""
    age: int | None = None
    description: str = ""
    personality: str = ""
    reference_images: dict[str, str] = Field(default_factory=dict, description="三视图定妆照 URL（front/side/closeup）")
    used_prompts: dict[str, str] = Field(default_factory=dict, description="定妆照生成时使用的提示词")
    appearance_lock: str = Field("", description="外观锁定卡：分镜生成时强制注入的核心外观关键词")
    locked: bool = Field(True, description="锁定后分镜/视频生成强制引用外观锁定卡")
    consistency_level: str = "L3"
    created_at: int = 0
    updated_at: int = 0


class CharacterAssetUpdateRequest(BaseModel):
    """角色资产局部更新请求（仅白名单字段生效）。"""

    name: str | None = None
    role: str | None = None
    age: int | None = None
    description: str | None = None
    personality: str | None = None
    appearance_lock: str | None = None
    locked: bool | None = None
    consistency_level: str | None = None


class MentionResolveRequest(BaseModel):
    """@角色提及解析请求（M24.1 主体库 @引用可视化）。

    分镜/视频提示词框输入的文本可含 `@角色名` 语法，本请求提交文本，
    由 mention_service 提取全部提及并映射到角色资产库。
    """

    text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="含 @角色名 的提示词文本（1-10000 字符）",
    )


# ====================================================================
# M27 NAS 模型库 / 模型下载 / NSFW 设置
# ====================================================================


class NasModelEntry(BaseModel):
    """NAS 模型库条目。"""

    name: str
    rel_path: str
    root: str
    type: str
    size: int
    mtime: float
    nsfw: bool


class ModelDownloadRequest(BaseModel):
    """模型下载请求。"""

    download_url: str = Field(..., min_length=8, description="直链（自动走 hf-mirror）")
    filename: str = Field(..., min_length=1, max_length=255)
    subdir: str = Field(..., description="ComfyUI 模型子目录（白名单）")
    sha256: str | None = Field(None, description="可选 SHA256 校验（hex）")
    nsfw: bool = Field(False, description="Civitai 搜索透传的 NSFW 标记")


class NsfwSetRequest(BaseModel):
    """NSFW 开关请求。首次开启需 new_pin 设置管理 PIN。"""

    enabled: bool
    pin: str = ""
    new_pin: str | None = None


class NsfwPinChangeRequest(BaseModel):
    """修改 NSFW 管理 PIN。"""

    pin: str = Field(..., min_length=1)
    new_pin: str = Field(..., min_length=4, max_length=8)


class MentionInfo(BaseModel):
    """单个 @提及的解析结果：角色ID / 角色名 / 定妆照 URL / 外观锁定卡。"""

    mention: str = Field(..., description="@后的原始提及文本（不含@）")
    matched: bool = Field(..., description="是否在角色资产库中匹配到角色")
    match_type: str = Field("", description="匹配方式：exact 精确 / ci 大小写不敏感 / fuzzy 模糊包含")
    character_id: str = ""
    name: str = ""
    reference_front: str = Field("", description="定妆照正面图 URL（三视图 front）")
    appearance_lock: str = Field("", description="外观锁定卡（仅锁定角色非空，截断 400 字符）")
    locked: bool = False


class MentionResolveData(BaseModel):
    """@提及解析完整结果（POST /assets/resolve-mentions 的 data 载荷）。"""

    text: str = Field(..., description="原始输入文本")
    mentions: list[MentionInfo] = Field(default_factory=list, description="全部提及解析结果（按出现顺序）")
    unmatched: list[str] = Field(default_factory=list, description="未匹配的提及名列表")
    reference_images: list[str] = Field(
        default_factory=list,
        description="已匹配角色的定妆照正面图 URL（去重，保持提及顺序），可直接入 VideoRequest.reference_images",
    )
    expanded_text: str = Field(
        ...,
        description="展开文本：已匹配锁定角色的外观锁定卡拼入前缀段 + 原文",
    )


class CharacterCard(BaseModel):
    """角色卡结构（对应 PLATFORM_SPEC §4.8）。"""

    character_id: str
    name: str
    anchor_points: int = 200
    feature_vector: list[float] = Field(default_factory=list)
    reference_images: dict[str, str] = Field(default_factory=dict)
    lora_path: str = ""
    voice_sample: str = ""
    consistency_level: str = "L2"
    used_prompts: dict[str, str] = Field(default_factory=dict, description="生成时使用的提示词（供前端编辑）")


class FailureMode(BaseModel):
    """失败模式定义（M25.9 C2 失败模式注册表，DramaClaw failure_registry 对等）。

    四元组：detection（VLM 判定问句）/ prevention_rule（预防规则）/
    correction_template（修正指令模板）/ negative_prompt_clause（反向子句）。
    gate_enabled=True 的高置信模式进入 VLM 门禁；hit_count 为项目命中计数
    （重复犯错者上浮，供提示词治理优先级排序）。
    """

    code: str
    layer: str = Field(..., description="分层：generator(生成)/correction(修正)/director(导演调度)")
    detection: str = Field(..., description="VLM 门禁判定问句")
    prevention_rule: str = ""
    correction_template: str = ""
    negative_prompt_clause: str = ""
    gate_enabled: bool = False
    hit_count: int = 0
    created_at: int = 0
    updated_at: int = 0


class CharacterRequest(BaseModel):
    """角色 Agent 输入。"""

    character: Character
    style: str = Field("写实电影感", description="画风")
    consistency_level: str = Field("L3", description="一致性层级 L1-L4")
    custom_positive_prompt: str = Field("", description="自定义正面提示词（非空时跳过 LLM 生成）")
    custom_negative_prompt: str = Field("", description="自定义负面提示词（非空时跳过 LLM 生成）")
    # 预览阶段传入已编辑的提示词，直接生成图片
    preview_positive_prompt: str = Field("", description="预览确认后的正面提示词（三视图共用）")
    preview_negative_prompt: str = Field("", description="预览确认后的负面提示词")


class CharacterPreviewRequest(BaseModel):
    """角色生成预览请求：搜索参考资料 + 生成提示词，不生成图片。"""

    character: Character
    style: str = Field("写实电影感", description="画风")


class CharacterPreviewResponse(BaseModel):
    """角色生成预览响应。"""

    character_id: str
    character: Character
    style: str
    search_reference: str = Field("", description="AI 联网搜索到的参考资料")
    prompts: dict[str, str] = Field(default_factory=dict, description="LLM 生成的三视图提示词")


class StoryboardRequest(BaseModel):
    """分镜 Agent 输入。"""

    scene: Scene
    characters: list[Character] = Field(default_factory=list)
    style: str = Field("写实电影感", description="画风")
    # M25.2 AutoLink：None=跟随全局 settings.auto_link_assets_enabled；
    # 显式 False 关闭本请求的自动资产匹配（回退 M24 前行为）
    auto_link_assets: bool | None = Field(
        None,
        description="AutoLink 自动资产匹配开关：None=跟随全局配置；False=关闭；True=强制开启",
    )
    # M25.9 C1 线稿先行：True=草图模式（低步数/低CFG/小尺寸快速出构图）；
    # 结果 sketch_seed 记录确定性锚点，供精渲染复用
    sketch_mode: bool = Field(
        False,
        description="草图模式：低步数快速出构图，用户确认后带 sketch_seed 精渲染",
    )
    # M25.9 C1 同 seed 防漂移：非空时精渲染复用该 seed（草图阶段返回的 seed）
    refine_seed: int | None = Field(
        None,
        description="精渲染复用的草图 seed（同 seed 保证构图不漂移）；None=随机",
    )


class StoryboardResult(BaseModel):
    """分镜 Agent 输出。"""

    scene_id: int
    image_url: str
    prompt_used: str = ""
    # 分镜预览视频 URL（预留：LTX-2.5 预览路径重建后填充）
    # 仅用于快速预览分镜动态效果，不替代正式视频生成
    preview_video_url: str = ""
    # M25.9 C1 线稿先行：本次生成是否草图；sketch_seed 为草图确定性锚点，
    # 前端确认构图后随 refine_seed 回传精渲染（同 seed 防构图漂移）
    is_sketch: bool = False
    sketch_seed: int | None = None


class StoryboardBatchRequest(BaseModel):
    """分镜批量生成请求：一次并行生成多个场景的关键帧。"""

    scenes: list[Scene] = Field(..., description="待生成分镜的场景列表")
    characters: list[Character] = Field(default_factory=list)
    style: str = Field("写实电影感", description="画风")
    # M25.2 AutoLink：批量级开关，语义同 StoryboardRequest.auto_link_assets，
    # 透传到每个场景的子请求
    auto_link_assets: bool | None = Field(
        None,
        description="AutoLink 自动资产匹配开关：None=跟随全局配置；False=关闭；True=强制开启",
    )


class StoryboardBatchResult(BaseModel):
    """分镜批量生成结果。"""

    results: list[StoryboardResult] = Field(default_factory=list)
    failed_scenes: list[int] = Field(default_factory=list)


class VideoRequest(BaseModel):
    """视频 Agent 输入。"""

    scene_id: int
    image_url: str = Field(..., description="分镜关键帧图片 URL")
    prompt: str = Field("", description="英文正面提示词")
    negative_prompt: str = Field("", description="英文反向提示词")
    duration_seconds: int = Field(3, description="视频时长（秒）")
    # H3 ref2va 触发条件：非空时走 MiniMaxH3ReferenceToVideo 角色一致性路径，
    # 分镜关键帧作第 1 张参考图（构图），本列表随后（角色外观锁定）
    reference_images: list[str] = Field(
        default_factory=list,
        description="角色参考图 URL 列表（三视图定妆照），非空时 H3 走 ref2va 角色一致性路径",
    )
    # M11 多镜分组元数据：同集（episode 相同）且相邻的场景才可并入同一多镜组
    episode: int = Field(1, description="所属集数（H3 多镜联合生成的分组依据）")
    # M12 多镜 SHOT prompt 节拍视觉化：合法值见 script_agent.VALID_NARRATIVE_BEATS，
    # 空串/非法值不注入节拍视觉指令（向后兼容逐场景路径）
    narrative_beat: str = Field("", description="叙事节拍（hook/escalation/reversal/cliffhanger/emotional_beat/transition）")
    # M17.3 FL2VA 末帧锚定：非空时 fl2va 工作流挂接 last_frame（首帧+末帧双锚定），
    # prompt 前置官方对齐指令（Picture 1 → 0.00s / Picture 2 → S.SSs）
    last_frame_url: str = Field("", description="末帧图片 URL（FL2VA 双锚定，空串退化为 I2VA 首帧单锚定）")
    # M17.4 ref2va 音视频参考（H3 全模态）：参考视频提供运镜/节奏/剪辑结构，
    # 独立参考音频提供 BGM 风格/声景质感（节点上限各 3，合计参考文件 ≤12）
    reference_videos: list[str] = Field(
        default_factory=list,
        description="参考视频 URL 列表（ref2va 运镜/节奏参考，节点上限 3）",
    )
    reference_audios: list[str] = Field(
        default_factory=list,
        description="独立参考音频 URL 列表（ref2va BGM 风格/声景参考，节点上限 3）",
    )
    # M18.4 H3 画风漂移治理：目标画风由 orchestrator 透传（与剧本/角色/分镜同源），
    # H3 prompt 冲突清洗 + 风格锚定 + 产出 VLM 画风质检的基准；空串跳过（向后兼容）
    style: str = Field("", description="目标画风（M18.4 H3 画风锚定/质检基准，空串跳过）")
    # M21 双引擎路由：显式指定视频引擎（None/'auto' 按镜头类型自动路由——
    # 对白/角色一致性 → H3；空镜/动作/长场景 → LTX-2.5）
    engine: str | None = Field(
        None,
        description="视频引擎：None/'auto' 自动路由 / 'h3' MiniMax H3 / 'ltx' LTX-2.5 / 'comfyui' Wan2.2 回退",
    )
    # M24.2 锚点重拍（单镜头参数锁定与复现）：
    # seed 对应 u64 语义（0 .. 2^64-1），None 表示由后端随机分配；
    # 单镜头重拍时填入首次生成的 seed，保证同镜头可复现、其余镜头不受影响
    seed: int | None = Field(
        None,
        ge=0,
        le=18446744073709551615,  # u64 上限
        description="生成随机种子（u64 语义：0..2^64-1，None=随机）。锚点重拍时固定以保证可复现",
    )
    # lock_params 对应 Option<serde_json::Value> 语义（JSON 对象）：
    # 锁定的生成参数快照（如 engine/sampler/steps/cfg），由 pipeline 步骤落盘
    # shot_params.json 记录；重拍时回传则视频 Agent 优先采用这些参数
    lock_params: dict[str, Any] | None = Field(
        None,
        description="锁定的生成参数快照（JSON 对象，如 engine/steps/sampler/cfg）。"
        "非空时视频 Agent 优先采用，用于单镜头锚点重拍复现；None=按当前默认参数",
    )


class VideoResult(BaseModel):
    """视频 Agent 输出。"""

    scene_id: int
    video_url: str
    duration_seconds: int = 3


class RerunShotRequest(BaseModel):
    """单镜头锚点重拍请求（M25.1）。

    从 output/pipeline/{project_id}/shot_params.json 恢复该镜头的
    生成参数快照（prompt/seed/engine/lock_params/reference_images 等），
    仅重跑目标镜头，其余镜头参数与产物不受影响。
    """

    project_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="流水线 project_id（shot_params.json 所在目录名）",
    )
    scene_id: int = Field(..., description="要重拍的镜头场景号")
    seed: int | None = Field(
        None,
        ge=0,
        le=18446744073709551615,  # u64 上限
        description="覆盖种子：None=沿用快照 seed；快照 seed 也为 None 时由后端随机",
    )
    reseed: bool = Field(
        False,
        description="换 seed 重拍：True 时忽略快照与 seed 字段，强制后端随机（与 seed 互斥，reseed 优先）",
    )
    override_prompt: str = Field(
        "",
        max_length=10000,
        description="覆盖提示词（非空时替换快照 prompt 重拍；空串沿用快照）",
    )


class VideoBatchRequest(BaseModel):
    """视频批量生成请求：一次并行生成多个场景的视频片段。"""

    items: list[VideoRequest] = Field(..., description="待生成视频的场景请求列表")


class VideoBatchResult(BaseModel):
    """视频批量生成结果。"""

    results: list[VideoResult] = Field(default_factory=list)
    failed_scenes: list[int] = Field(default_factory=list)


class DialogueLine(BaseModel):
    """单条台词配音输入。"""

    text: str = Field(..., description="台词文本")
    character_name: str = Field("", description="角色名")
    character_role: str = Field("", description="角色定位（主角/反派/旁白等）")
    character_age: int | None = Field(None, description="角色年龄")
    rate: str = Field("+0%", description="语速调整，如 +10% / -10%")


class VoiceRequest(BaseModel):
    """配音 Agent 输入。"""

    scene_id: int
    dialogues: list[DialogueLine] = Field(..., description="台词列表")


class VoiceResult(BaseModel):
    """配音 Agent 输出。"""

    scene_id: int
    audio_urls: list[dict[str, Any]] = Field(default_factory=list)
    total_lines: int = 0


class SubtitleRequest(BaseModel):
    """字幕 Agent 输入。"""

    scene_id: int
    audio_url: str = Field(..., description="音频文件 URL")
    language: str = Field("zh", description="语言代码（zh/en）")


class SubtitleSegment(BaseModel):
    """字幕片段。"""

    start: float
    end: float
    text: str


class SubtitleResult(BaseModel):
    """字幕 Agent 输出。"""

    scene_id: int
    srt_content: str = ""
    segments: list[SubtitleSegment] = Field(default_factory=list)
    language: str = "zh"


class EditSegment(BaseModel):
    """剪辑片段输入：一个场景的视频/音频/字幕素材。"""

    scene_id: int
    video_url: str = Field(..., description="视频片段 URL")
    audio_url: str = Field(..., description="配音音频 URL")
    subtitle_url: str = Field("", description="SRT 字幕 URL（可空，空则不烧字幕）")
    duration_seconds: int = Field(5, description="目标时长（以音频为准）")


class EditRequest(BaseModel):
    """剪辑 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="成片标题")
    segments: list[EditSegment] = Field(..., description="按顺序排列的片段素材")
    transition: str = Field("none", description="转场类型：none / fade")
    bgm_url: str | None = Field(None, description="背景音乐 URL（可选）")
    output_resolution: str = Field("1080x1920", description="输出分辨率，如 1080x1920")
    output_fps: int = Field(24, ge=1, le=60, description="输出帧率")
    # 2026-09-01 新规：AI 生成微短剧须在每集明显位置添加提示标识
    ai_label_enabled: bool = Field(True, description="是否在成片右上角烧录「AI生成」标识（合规要求默认开启）")
    license_number: str = Field("", description="短剧备案号/节目编号（非空时随标识一并烧录）")


class EditResult(BaseModel):
    """剪辑 Agent 输出。"""

    project_id: str
    title: str
    final_video_url: str
    duration_seconds: float
    segments_count: int


class PipelineRunRequest(BaseModel):
    """M7 全链路自动编排请求：从一句话创意一键生成短剧成片。"""

    premise: str = Field(..., description="一句话创意", min_length=1)
    genre: str = Field("都市悬疑", description="题材")
    episodes: int = Field(1, ge=1, le=10, description="集数")
    scenes_per_episode: int = Field(3, ge=1, le=10, description="每集分镜数")
    monetization_mode: str = Field("iaa", description="变现模式：iaa / iap")
    style: str = Field("写实电影感", description="画风")
    # M21.3 长视频模式：standard(逐场景生成) / long(LongVideoPlanner 拆块 + 帧链续写，
    # 需 settings.long_video_enabled=True；视觉轨整体产出，配音/字幕步骤跳过)
    video_mode: str = Field("standard", description="视频生成模式：standard / long")
    generate_character_refs: bool = Field(True, description="是否生成角色定妆照（耗时较长）")
    max_character_refs: int = Field(2, ge=0, le=10, description="最多生成定妆照的角色数")
    video_duration_seconds: int = Field(3, ge=1, le=10, description="单镜头视频时长（秒）")
    run_quality_check: bool = Field(True, description="成片后是否执行文本质检")
    # M13 角色一致性对照视觉检测（VLM 逐场景对照角色定妆参考图，耗时较长，默认关闭）
    run_visual_check: bool = Field(False, description="成片后是否执行视觉质检（角色漂移对照）")
    ai_label_enabled: bool = Field(True, description="成片烧录「AI生成」标识（合规默认开启）")
    license_number: str = Field("", description="短剧备案号（非空时随标识烧录）")
    output_resolution: str = Field("1080x1920", description="输出分辨率")
    output_fps: int = Field(24, ge=1, le=60, description="输出帧率")
    # M17.4 H3 全模态参考（仅 video_backend=h3 生效）：参考视频提供运镜/节奏/剪辑
    # 结构，独立参考音频提供 BGM 风格/声景质感；透传到每个 VideoRequest 走 ref2va
    reference_videos: list[str] = Field(
        default_factory=list, description="参考视频 URL 列表（H3 ref2va 运镜/节奏参考，≤3）"
    )
    reference_audios: list[str] = Field(
        default_factory=list, description="独立参考音频 URL 列表（H3 ref2va BGM 风格参考，≤3）"
    )


class AsyncTaskResponse(BaseModel):
    """异步任务创建响应。"""

    task_id: str
    agent: str
    status: str = "pending"
    poll_url: str = ""
    stream_url: str = ""


class QualityCheckItem(BaseModel):
    """单条质检问题。"""

    category: str = Field(..., description="问题类别：consistency/logic/sensitive/grammar")
    severity: str = Field(..., description="严重级别：info/warning/critical")
    scene_id: int | None = Field(None, description="关联场景 ID（可选）")
    message: str = Field(..., description="问题描述")
    suggestion: str = Field("", description="修改建议")


class QualityCheckRequest(BaseModel):
    """质检 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="剧本标题")
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    subtitles: list[SubtitleResult] = Field(default_factory=list)
    check_types: list[str] = Field(
        default_factory=lambda: ["consistency", "logic", "sensitive"],
        description="检查类型列表",
    )


class QualityCheckResult(BaseModel):
    """质检 Agent 输出。"""

    project_id: str
    title: str
    score: int = Field(..., ge=0, le=100, description="综合质量分")
    summary: str = ""
    issues: list[QualityCheckItem] = Field(default_factory=list)
    checked_at: float = Field(default_factory=time.time)


class QualityVisualItem(BaseModel):
    """视觉质检单条问题。"""

    category: str = Field(..., description="问题类别：visual_consistency/coherence/anomaly/subtitle")
    severity: str = Field(..., description="严重级别：info/warning/critical")
    timestamp: float | None = Field(None, description="问题发生的时间戳（秒），可选")
    message: str = Field(..., description="问题描述")
    suggestion: str = Field("", description="修改建议")


class QualityVisualRequest(BaseModel):
    """视觉质检 Agent 输入。"""

    project_id: str = Field("", description="项目 ID")
    title: str = Field("", description="视频标题")
    scene_id: int = Field(0, description="场景 ID")
    video_url: str = Field(..., description="待检查的视频 URL")
    check_types: list[str] = Field(
        default_factory=lambda: ["visual_consistency", "coherence", "anomaly"],
        description="检查类型列表",
    )
    max_frames: int = Field(8, ge=1, le=32, description="最大抽帧数")
    # M13 角色一致性对照：角色资产库定妆三视图参考图 URL 列表，
    # 非空时 VLM 将视频帧与参考图逐一对照，判定角色外观漂移
    reference_image_urls: list[str] = Field(
        default_factory=list,
        description="角色定妆参考图 URL 列表（空列表则不做对照检测）",
    )


class QualityVisualResult(BaseModel):
    """视觉质检 Agent 输出。"""

    project_id: str
    title: str
    scene_id: int
    score: int = Field(..., ge=0, le=100, description="视觉质量分")
    summary: str = ""
    issues: list[QualityVisualItem] = Field(default_factory=list)
    checked_at: float = Field(default_factory=time.time)
    # M13 角色漂移实锤判定（仅 reference_image_urls 非空时由 VLM 输出，缺省 False）
    drift_detected: bool = Field(False, description="是否检测到角色外观相对参考图漂移")


class SubtitleFixRequest(BaseModel):
    """字幕回写修正请求：基于质检 issues 自动修正 ASR 错别字。"""

    subtitles: list[SubtitleResult] = Field(..., description="待修正的字幕列表")
    issues: list[QualityCheckItem] = Field(..., description="质检发现的问题列表")
    persist: bool = Field(True, description="是否回写到 SRT 文件")


class SubtitleFixItem(BaseModel):
    """单条字幕修正结果。"""

    scene_id: int
    original_text: str = Field("", description="修正前文本片段")
    fixed_text: str = Field("", description="修正后文本片段")
    applied: list[dict[str, str]] = Field(
        default_factory=list, description="应用的修正对 [{wrong, right}]"
    )


class SubtitleFixResult(BaseModel):
    """字幕回写修正结果。"""

    fixed_subtitles: list[SubtitleResult] = Field(default_factory=list)
    corrections: list[dict[str, str]] = Field(
        default_factory=list, description="提取到的全部修正对 [{wrong, right}]"
    )
    fixed_count: int = Field(0, description="被修改的字幕段数")
    details: list[SubtitleFixItem] = Field(default_factory=list)
    persisted_files: list[str] = Field(default_factory=list, description="回写的 SRT 文件路径")


class AgentResponse(BaseModel):
    """Agent 统一响应。"""

    success: bool
    data: Any = None
    error: str | None = None
    elapsed_seconds: float = 0.0


class PipelineTemplateItem(BaseModel):
    """类型片叙事镜头模板（M25.3 模板起手，genre_tropes 知识库条目）。"""

    id: str = Field(..., description="模板唯一标识（知识库条目 id）")
    title: str = Field("", description="模板标题（如「霸总对峙/壁咚」）")
    category: str = Field("genre_trope", description="模板类别，默认 genre_trope")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    summary: str = Field("", description="模板内容摘要（截断 200 字符）")
    content: str = Field("", description="模板完整内容（预填创意输入框用）")


class PipelineTemplateListResponse(BaseModel):
    """模板库列表响应（GET /pipeline/templates）。"""

    templates: list[PipelineTemplateItem] = Field(default_factory=list)
    total: int = Field(0, description="返回模板总数")
    categories: list[str] = Field(default_factory=list, description="全部可用类别（供前端筛选）")


class RAGOptimizeRequest(BaseModel):
    """RAG 提示词优化请求。"""

    user_prompt: str = Field(..., description="用户原始中文/英文描述")
    domain: str = Field("video", description="目标领域：image / video")
    style_hint: str = Field("", description="风格提示，用于增强检索")
    extra_instruction: str = Field("", description="额外优化指令")


class RAGOptimizeResponse(BaseModel):
    """RAG 提示词优化响应。"""

    optimized_positive: str = Field("", description="优化后的英文正向提示词")
    optimized_negative: str = Field("", description="优化后的英文负向提示词")
    style_notes: str = Field("", description="中文风格/技法说明")
    tags: list[str] = Field(default_factory=list, description="检索/分类标签")
    lora_recommendations: list[dict[str, Any]] = Field(
        default_factory=list, description="推荐 LoRA 列表（filename/style_key/trigger_words/weight）"
    )
    original_prompt: str = Field("", description="原始用户描述")
    retrieved_count: int = Field(0, description="检索到的知识条目数")
    fallback: bool = Field(False, description="是否使用了兜底结果")


class AgentAssistRequest(BaseModel):
    """通用智能体辅助请求：对指定上下文中的文本进行润色/扩写/精简/改写。"""

    text: str = Field(..., description="待处理的原始文本", min_length=1)
    context: str = Field(..., description="上下文类型：script/character/storyboard/video/voice/subtitle/edit/quality")
    action: str = Field("polish", description="动作：polish(润色)/expand(扩写)/shorten(精简)/rewrite(改写)")
    extra_instruction: str = Field("", description="额外要求")


class AgentAssistResponse(BaseModel):
    """通用智能体辅助响应。"""

    text: str = Field("", description="处理后的文本")
    action: str = Field("", description="执行的动作")
    context: str = Field("", description="上下文类型")
