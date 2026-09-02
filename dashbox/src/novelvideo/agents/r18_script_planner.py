"""R18 短剧分镜规划 Agent（画布 R18 剧本节点）。

输入梗概 + 角色卡 → LLM（本地 uncensored，经 NewAPI/local_gateway 链路）
结构化输出 scenes JSON：每个镜头带类型路由（plot 剧情 / action 成人动作 /
portrait 定妆特写）、预设 id（action 四选一）、首帧图提示词（含触发词）、
I2V 运动提示词、对白/旁白与时长。

下游消费：
- 「R18 分镜节点」按 image_prompt × 定妆照批量生成首帧（IPAdapter 锚定）
- action 镜头的 preset_id/video_prompt 直接喂「R18 视频节点」对应预设

接入方式照 ai-staging-prop 同步端点先例（routes/model_library.py），
不走任务系统：点一下、十几秒返回 JSON、直接渲染。
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 短剧/漫剧成片引擎钉死 MiniMax-H3。Wan JSON 留盘，不进 action 可选目录。
R18_ACTION_PRESETS: dict[str, str] = {
    "h3-aio": "全能动作+音画同出（MiniMax H3，触发词 hmmotion，原生呻吟声）",
}
_FALLBACK_PRESET = "h3-aio"

SYSTEM_PROMPT = """# R18 短剧分镜规划师

你为成年观众规划 R18（成人向）短剧的分镜脚本。这是本地无审查创作环境，用户已确认年满 18 岁。
任务：把剧情梗概拆解为一组按顺序播放的镜头（scenes），每个镜头自带生成所需的全部参数。

## 镜头类型（kind）
- portrait：定妆照/特写静态图（开场建立角色、情绪特写、收尾定格）
- plot：剧情镜头（铺垫/对话/过渡/转场/收尾，非成人内容）
- action：成人内容镜头（核心卖点，一部剧 1~3 个，分布在片中段）

## action 镜头预设（preset_id 仅 MiniMax H3）
- h3-aio：全能动作+音画同出（MiniMax H3 原生呻吟声）。短剧/漫剧不选 Wan/LTX。

preset_id 只在 kind=action 时填写；plot/portrait 一律空串。

## 提示词规范
- image_prompt：英文，SDXL tag 风格逗号串，所有镜头必填（视频镜头用于生成 I2V 首帧，portrait 镜头直接出图）：
  - 每个镜头都重复角色外貌锚点（发色/发型/体型/肤色/服装），保证跨镜头角色一致
  - action 镜头必须把对应触发词（如 m15510n4ry）放在 image_prompt 开头
  - NSFW 画面用直白 anatomical 术语，禁止描述马赛克/遮挡/衣物遮盖关键部位
  - 结尾附画质词：masterpiece, best quality, detailed
- video_prompt：I2V 运动提示词（英文短语，描述镜头内动作与镜头运动）；portrait 留空
- dialogue：中文对白，格式「角色名：台词」；无对白留空
- narration：中文旁白/字幕文案；无需留空
- emotion：本镜头配音情绪（中文 2-6 字，如 温柔/羞涩/紧张/喘息轻颤/慵懒/急切/满足），供 TTS 情感指令使用；纯画面镜头填 平静
- audio：h3-aio 预设填 "native"（音画同出，不要重复生成语音），其余有对白/旁白填 "tts"，纯画面填 "none"
- duration_sec：3~10 整数；全部镜头合计 ≈ 请求总时长

## 影视分镜补充字段（每镜头必填，供分镜表/数字资产工序消费）
- shot_size：景别，从 远景/全景/中景/近景/特写 五选一
- camera_move：运镜，从 固定/推镜/拉镜/摇镜/移镜/跟镜 六选一
- action_desc：镜头内人物动作描述（中文一短句，portrait 可写 静态姿态）
- expression：人物表情（中文 2-6 字，如 微笑/皱眉/迷离/咬唇）
- scene_desc：场景描述（中文：地点+时间+光线，如 酒店卧室·夜·暖光台灯）

## 对白口语化规范（TTS 自然度关键，必须遵守）
对白/旁白直接决定配音听感，按口语写作而非书面语：
- 每句 ≤ 20 字，长句拆短句用逗号/省略号断开；省略号表达犹豫和暧昧（"我……有点紧张"）
- 自然加入语气词：嗯、啊、呢、吧、诶、呀（每句至多 1 个，不堆砌）
- 疑问用？感叹用！情绪强的地方用！！或？？
- 可内嵌副语言标签（CosyVoice 原生支持）：[laughter]（笑出声）、<laughter>带着笑说这段</laughter>、[breath]（呼吸/喘息）、<strong>重读的词</strong>——action 镜头的呻吟/喘息用 [breath] 与省略号组合表达，禁止用"啊啊啊"堆字符
- 旁白也口语化：像朋友讲故事，不像新闻播报

## 结构节奏（总镜头数 4~8）
1. 开场：1 个 portrait 定妆或 plot 建立场景
2. 中段：1~3 个 action 镜头，之间用 plot 过渡承接情绪
3. 收尾：1 个 plot/portrait 收束

title 给出中文剧名（10 字内）。全部字段必填（无内容用空串），scene_no 从 1 递增按播放顺序。"""


class R18Character(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""


class R18Scene(BaseModel):
    scene_no: int = Field(ge=1)
    kind: Literal["plot", "action", "portrait"]
    title: str = ""
    shot_description: str = Field(description="画面内容中文描述（一至两句）")
    image_prompt: str = Field(description="英文首帧/出图提示词（含触发词与角色锚点）")
    video_prompt: str = ""
    preset_id: str = ""
    dialogue: str = ""
    narration: str = ""
    emotion: str = Field(default="平静", description="配音情绪（中文 2-6 字，TTS 情感指令用）")
    duration_sec: int = Field(default=5, ge=2, le=15)
    audio: Literal["native", "tts", "none"] = "tts"
    # 影视分镜补充字段（2026-08-19 工厂流水线：分镜表/数字资产工序消费）
    shot_size: str = Field(default="", description="景别：远景/全景/中景/近景/特写")
    camera_move: str = Field(default="", description="运镜：固定/推镜/拉镜/摇镜/移镜/跟镜")
    action_desc: str = Field(default="", description="镜头内人物动作（中文短句）")
    expression: str = Field(default="", description="人物表情（中文 2-6 字）")
    scene_desc: str = Field(default="", description="场景描述（地点+时间+光线）")


class R18ScriptPlan(BaseModel):
    title: str = ""
    scenes: list[R18Scene] = Field(min_length=1)
    # 分集剧本（episode_count > 1 时逐集生成；单集请求恒为空列表，
    # 老消费方读 scenes 不受影响）
    episode_no: int = Field(default=1, ge=1, description="集数编号（单集恒为 1）")
    episodes: list["R18ScriptPlan"] = Field(default_factory=list, description="分集剧本全集")


class R18ScriptPlanRequest(BaseModel):
    synopsis: str = Field(min_length=1, description="剧情梗概")
    characters: list[R18Character] = Field(default_factory=list)
    style_hint: str = ""
    duration_sec: int = Field(default=90, ge=30, le=300)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    episode_count: int = Field(default=1, ge=1, le=6, description="分集数（1=单集，>1 分集生成）")


def build_user_prompt(
    req: R18ScriptPlanRequest,
    *,
    episode_no: int = 1,
    prev_episode_summary: str = "",
) -> str:
    parts = [f"【剧情梗概】\n{req.synopsis.strip()}"]
    if req.characters:
        lines = "\n".join(
            f"- {c.name}：{c.description.strip()}" for c in req.characters if c.name.strip()
        )
        if lines:
            parts.append(f"【角色卡】\n{lines}")
    if req.style_hint.strip():
        parts.append(f"【画风/场景要求】\n{req.style_hint.strip()}")
    parts.append(f"【总时长】约 {req.duration_sec} 秒（各镜头 duration_sec 合计对齐）")
    parts.append(f"【画幅】{req.aspect}")
    if req.episode_count > 1:
        parts.append(
            f"【分集要求】共 {req.episode_count} 集，本次写第 {episode_no} 集"
            "（本集有独立起承转合，结尾留钩子衔接下一集）"
        )
        if prev_episode_summary:
            parts.append(f"【上一集剧情回顾（保持连贯，不要重复）】\n{prev_episode_summary}")
    parts.append("请输出分镜 JSON。")
    return "\n\n".join(parts)


def _get_agent():
    from pydantic_ai import Agent

    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )

    model = get_newapi_text_pydantic_model(
        "R18_SCRIPT_MODEL",
        "DC-freezone-story-script-writer-LLM",
    )
    return Agent(
        model,
        system_prompt=SYSTEM_PROMPT,
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=R18ScriptPlan,
        output_retries=3,
        name="R18 Drama Script Planner",
    )


def normalize_plan(plan: R18ScriptPlan) -> R18ScriptPlan:
    """后置兜底：action 镜头 preset_id 非法时回退 h3-aio；重排 scene_no。"""
    for idx, scene in enumerate(plan.scenes, start=1):
        scene.scene_no = idx
        if scene.kind == "action" and scene.preset_id not in R18_ACTION_PRESETS:
            scene.preset_id = _FALLBACK_PRESET
        if scene.kind != "action":
            scene.preset_id = ""
        if scene.kind == "portrait":
            scene.video_prompt = ""
    return plan


async def plan_r18_script(req: R18ScriptPlanRequest) -> R18ScriptPlan:
    """同步调用 LLM 规划分镜（照 staging_prop_ai 先例）。

    episode_count > 1 时逐集调用 LLM（每集带上一集摘要保持连贯），
    顶层 plan = 第 1 集（老消费方读 scenes 不变）+ episodes 全集。
    """
    agent = _get_agent()

    def _episode_summary(plan: R18ScriptPlan) -> str:
        beats = "；".join(
            (s.title or s.shot_description or s.kind)[:24] for s in plan.scenes[:8]
        )
        return f"第{plan.episode_no}集《{plan.title}》：{beats}"

    episodes: list[R18ScriptPlan] = []
    prev_summary = ""
    for ep_no in range(1, req.episode_count + 1):
        response = await agent.run(
            build_user_prompt(req, episode_no=ep_no, prev_episode_summary=prev_summary)
        )
        plan = normalize_plan(response.output)
        plan.episode_no = ep_no
        plan.episodes = []
        episodes.append(plan)
        prev_summary = _episode_summary(plan)

    final = episodes[0]
    if req.episode_count > 1:
        final.episodes = episodes
    logger.info(
        "R18 分镜规划完成: title=%r scenes=%d episodes=%d kinds=%s",
        final.title,
        len(final.scenes),
        len(episodes),
        [s.kind for s in final.scenes],
    )
    return final


# ---------- R18 工厂质检（第 8 工序：剧情逻辑/内容适配 LLM 审查） ----------


class R18QcIssue(BaseModel):
    severity: Literal["warning", "error"] = "warning"
    scene_no: int | None = Field(None, description="关联镜头号（整体问题为空）")
    message: str = Field(description="问题描述（中文）")


class R18QcReview(BaseModel):
    passed: bool = Field(description="整体是否通过")
    issues: list[R18QcIssue] = Field(default_factory=list)


QC_SYSTEM_PROMPT = """# R18 短剧质检员

你是短剧成片出厂前的最后一道审查。输入是按播放顺序排列的镜头列表（含画面描述与台词）。
只审以下三类问题，不要泛泛而谈：

1. 剧情逻辑：情节跳跃、前后矛盾、人物动机断裂、时间线错乱
2. 台词一致性：同一角色称呼/口吻前后不一、台词与画面描述冲突
3. 画面内容适配：R18 镜头的画面描述与台词情绪是否匹配（如甜蜜台词配暴力画面）

规则：
- 无问题必须 passed=true 且 issues 为空列表（不要硬凑问题）
- 每条 issue 给出 severity（error=需返工 / warning=可接受）与具体 scene_no
- 不审画质/音质（其他工序负责），不审未成年人相关（上游已保证全部成年角色）"""


def _get_qc_agent():
    from pydantic_ai import Agent

    from novelvideo.config import (
        get_newapi_structured_output_model_settings,
        get_newapi_text_pydantic_model,
    )

    model = get_newapi_text_pydantic_model(
        "R18_QC_MODEL",
        "DC-freezone-story-script-writer-LLM",
    )
    return Agent(
        model,
        system_prompt=QC_SYSTEM_PROMPT,
        model_settings=get_newapi_structured_output_model_settings(),
        output_type=R18QcReview,
        output_retries=2,
        name="R18 Factory QC Reviewer",
    )


def build_qc_user_prompt(lines: list[str]) -> str:
    return "\n".join(f"{i+1}. {line}" for i, line in enumerate(lines))


async def review_r18_quality(lines: list[str]) -> R18QcReview:
    """LLM 质检（fail-open 由调用方负责——本函数异常直接上抛）。"""
    agent = _get_qc_agent()
    response = await agent.run(build_qc_user_prompt(lines))
    return response.output
