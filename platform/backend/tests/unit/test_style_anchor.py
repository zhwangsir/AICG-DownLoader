"""画风锚定服务单元测试（M15.1：全链路画风一致性）。

覆盖：title/id/包含/tags 四级匹配、写实性分类、未知/空值兜底。
"""

from __future__ import annotations

from app.services.style_anchor import (
    DEFAULT_STYLE_TITLE,
    SDXL_CHECKPOINT_ANIME,
    SDXL_CHECKPOINT_REALISTIC,
    StyleAnchor,
    resolve_style_anchor,
    sanitize_style_conflicts,
    sdxl_checkpoint_for_anchor,
    strip_kb_atmosphere,
    style_negative_tail,
    style_positive_tail,
    style_prompt_clause,
)


class TestResolveStyleAnchor:
    def test_exact_title_realistic(self):
        anchor = resolve_style_anchor("写实电影感")
        assert anchor.title == "写实电影感"
        assert anchor.is_realistic is True
        assert "photorealistic" in anchor.realism_tail_en
        assert "cinematic realistic" in anchor.keywords_en

    def test_exact_title_non_realistic_guoman(self):
        anchor = resolve_style_anchor("国漫")
        assert anchor.title == "国漫"
        assert anchor.is_realistic is False
        assert anchor.realism_tail_en == ""
        assert "guoman" in anchor.keywords_en.lower()
        # 非写实风格的冲突负面词应含 realistic/photorealistic
        assert "photorealistic" in anchor.negative_en

    def test_title_space_insensitive(self):
        """'卡通3D' 应命中 KB 条目 '卡通 3D'（归一化去空白）。"""
        anchor = resolve_style_anchor("卡通3D")
        assert anchor.title == "卡通 3D"
        assert anchor.is_realistic is False
        assert anchor.style_name_en.lower().startswith("3d cartoon")

    def test_id_match(self):
        anchor = resolve_style_anchor("style_anime")
        assert anchor.title == "动漫"
        assert anchor.is_realistic is False
        assert "anime" in anchor.style_name_en.lower()

    def test_containment_match(self):
        """传入比 title 更长的描述串时，按包含关系命中。"""
        anchor = resolve_style_anchor("赛博朋克风格")
        assert anchor.title == "赛博朋克"
        assert anchor.is_realistic is True  # negative_terms 无写实冲突词 → 默认写实

    def test_tag_match(self):
        """tags 命中：'皮克斯' 是卡通 3D 的 tag。"""
        anchor = resolve_style_anchor("皮克斯")
        assert anchor.title == "卡通 3D"

    def test_unknown_style_falls_back_to_default(self):
        anchor = resolve_style_anchor("不存在的画风xyz")
        assert anchor.title == DEFAULT_STYLE_TITLE
        assert anchor.is_realistic is True

    def test_empty_and_none_fall_back_to_default(self):
        assert resolve_style_anchor("").title == DEFAULT_STYLE_TITLE
        assert resolve_style_anchor(None).title == DEFAULT_STYLE_TITLE

    def test_style_name_en_is_first_keyword_segment(self):
        anchor = resolve_style_anchor("国漫")
        assert anchor.style_name_en == "Chinese anime guoman style"


class TestStyleTails:
    """M15.1: style_positive_tail / style_negative_tail 锚定尾巴生成。"""

    def test_positive_tail_realistic(self):
        anchor = resolve_style_anchor("写实电影感")
        assert style_positive_tail(anchor) == (
            ", cinematic realistic, photorealistic, professional photography"
        )

    def test_positive_tail_non_realistic_has_no_realism_tail(self):
        """非写实风格只带风格名，不追加 photorealistic 画质尾。"""
        anchor = resolve_style_anchor("国漫")
        assert style_positive_tail(anchor) == ", Chinese anime guoman style"

    def test_negative_tail_non_realistic_rejects_realistic(self):
        anchor = resolve_style_anchor("国漫")
        tail = style_negative_tail(anchor)
        assert tail.startswith(", ")
        assert "photorealistic" in tail

    def test_negative_tail_realistic_rejects_anime(self):
        anchor = resolve_style_anchor("写实电影感")
        tail = style_negative_tail(anchor)
        assert "anime" in tail
        assert "3d render" in tail

    def test_empty_fields_return_empty_string(self):
        anchor = StyleAnchor(
            key="k",
            title="t",
            keywords_en="",
            style_name_en="",
            negative_en="",
            is_realistic=False,
        )
        assert style_positive_tail(anchor) == ""
        assert style_negative_tail(anchor) == ""


class TestSanitizeStyleConflicts:
    """M15.4: sanitize_style_conflicts 清洗 LLM 产出中与目标画风互斥的风格词。

    背景：core 国漫 E2E（pipeline-088b6ccb4b9b）中，剧本 LLM 场景 prompt 自带
    hyperrealistic、负面词反向排斥 anime/cartoon，导致风格尾被正文冲突信号抵消，
    角色定妆照（国漫）与 H3 视频（写实）仍脱节（drift_scenes=[2]）。
    """

    def test_positive_non_realistic_strips_realism_terms(self):
        """目标国漫 → 正向词删除 hyperrealistic/photorealistic 等写实词，保留其余内容。"""
        anchor = resolve_style_anchor("国漫")
        text = (
            "extreme close-up, two hands grasping a coffee cup, "
            "hyperrealistic texture, cinematic lighting, high contrast"
        )
        cleaned = sanitize_style_conflicts(text, anchor)
        assert "hyperrealistic" not in cleaned
        assert "photorealistic" not in cleaned
        assert "extreme close-up" in cleaned
        assert "texture" in cleaned
        assert "cinematic lighting" in cleaned

    def test_negative_non_realistic_strips_anime_terms(self):
        """目标国漫 → 反向词删除 anime/cartoon/3d render（不得排斥目标画风本身）。"""
        anchor = resolve_style_anchor("国漫")
        text = (
            "anime, cartoon, illustration, painting, 3d render, cgi, "
            "unrealistic, blurry, low quality, bad anatomy"
        )
        cleaned = sanitize_style_conflicts(text, anchor, negative=True)
        for term in ("anime", "cartoon", "illustration", "painting", "3d render", "cgi"):
            assert term not in cleaned.lower()
        # 质量负面词必须保留
        assert "blurry" in cleaned
        assert "low quality" in cleaned
        assert "bad anatomy" in cleaned

    def test_negative_non_realistic_keeps_unrealistic(self):
        """词边界匹配：unrealistic 不得被误删为 un。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts("unrealistic, dreamy, blurry", anchor, negative=True)
        assert "unrealistic" in cleaned
        assert "dreamy" in cleaned

    def test_positive_realistic_strips_anime_terms(self):
        """目标写实 → 正向词删除 anime/cartoon 等动漫词。"""
        anchor = resolve_style_anchor("写实电影感")
        text = "a young man in a convenience store, anime style, cinematic lighting"
        cleaned = sanitize_style_conflicts(text, anchor)
        assert "anime" not in cleaned.lower()
        assert "convenience store" in cleaned
        assert "cinematic lighting" in cleaned

    def test_negative_realistic_strips_realism_terms(self):
        """目标写实 → 反向词删除 photorealistic（不得排斥目标画风）。"""
        anchor = resolve_style_anchor("写实电影感")
        text = "photorealistic, anime, blurry, low quality"
        cleaned = sanitize_style_conflicts(text, anchor, negative=True)
        assert "photorealistic" not in cleaned
        # 写实目标的反向词保留 anime（排斥动漫是正确方向）
        assert "anime" in cleaned
        assert "blurry" in cleaned

    def test_punctuation_cleanup(self):
        """删除词后残留标点应收口：无连续逗号、无首尾逗号、逗号间距规范。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts(
            "hyperrealistic, coffee cup,photorealistic", anchor
        )
        assert cleaned == "coffee cup"

    def test_empty_and_blank_passthrough(self):
        anchor = resolve_style_anchor("国漫")
        assert sanitize_style_conflicts("", anchor) == ""
        assert sanitize_style_conflicts("   ", anchor) == "   "

    def test_multiword_term_with_hyphen(self):
        """多词/连字符词：hyper-realistic、film still 均可命中。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts(
            "portrait, hyper-realistic skin, film still, soft light", anchor
        )
        assert "hyper-realistic" not in cleaned
        assert "film still" not in cleaned
        assert "soft light" in cleaned

    def test_cinematic_realism_stripped(self):
        """M15.5：core E2E（pipeline-3ba8b3b3e304）实测残留 — cinematic realism 必须命中。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts(
            "shallow depth of field, cinematic realism, 24fps, smooth camera motion",
            anchor,
        )
        assert "cinematic realism" not in cleaned
        assert "24fps" in cleaned
        assert "smooth camera motion" in cleaned

    def test_bare_realistic_and_realism_stripped(self):
        """M15.5：裸 realistic / realism 也属写实家族（词边界保护 unrealistic/surrealism）。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts(
            "realistic proportions, realism, vivid colors", anchor
        )
        assert "realistic" not in cleaned
        assert "realism" not in cleaned
        assert "proportions" in cleaned
        assert "vivid colors" in cleaned

    def test_word_boundary_protects_unrealistic_and_surrealism(self):
        """词边界：unrealistic / surrealism 不得被裸 realistic / realism 误删。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = sanitize_style_conflicts(
            "unrealistic, surrealism, dreamy, blurry", anchor, negative=True
        )
        assert "unrealistic" in cleaned
        assert "surrealism" in cleaned
        assert "dreamy" in cleaned

    def test_realistic_target_keeps_realistic_in_positive(self):
        """写实目标正向词保留 realistic（只清洗动漫家族词）。"""
        anchor = resolve_style_anchor("写实电影感")
        cleaned = sanitize_style_conflicts(
            "a young man, realistic skin texture, cinematic realism", anchor
        )
        assert "realistic skin texture" in cleaned
        assert "cinematic realism" in cleaned


class TestSdxlCheckpointSelection:
    """M15.7: 按画风写实性选择 SDXL checkpoint。

    背景：core E2E（pipeline-1a92d5f7a966）国漫任务中，角色/分镜 SDXL 工作流
    硬编码 majicMIX（真人摄影特化），即使提示词带国漫锚定尾仍产出写实图，
    drift 无法通过 prompt 层根治 —— 必须按画风换模型。
    """

    def test_non_realistic_styles_use_animagine(self):
        """国漫/日漫/卡通3D 等非写实画风 → animagineXL40。"""
        for style in ("国漫", "日漫", "卡通3D"):
            anchor = resolve_style_anchor(style)
            assert anchor.is_realistic is False, style
            assert sdxl_checkpoint_for_anchor(anchor) == SDXL_CHECKPOINT_ANIME

    def test_realistic_styles_use_majicmix(self):
        """写实电影感/都市情感等写实画风 → majicMIX。"""
        for style in ("写实电影感", "都市情感"):
            anchor = resolve_style_anchor(style)
            assert anchor.is_realistic is True, style
            assert sdxl_checkpoint_for_anchor(anchor) == SDXL_CHECKPOINT_REALISTIC

    def test_none_anchor_falls_back_to_realistic(self):
        """anchor 为 None（未解析）→ 写实兜底（与 DEFAULT_STYLE_TITLE 一致）。"""
        assert sdxl_checkpoint_for_anchor(None) == SDXL_CHECKPOINT_REALISTIC

    def test_unknown_style_falls_back_to_realistic_checkpoint(self):
        """未知画风回退默认「写实电影感」→ majicMIX。"""
        anchor = resolve_style_anchor("不存在的画风xyz")
        assert anchor.title == DEFAULT_STYLE_TITLE
        assert sdxl_checkpoint_for_anchor(anchor) == SDXL_CHECKPOINT_REALISTIC


class TestStylePromptClause:
    """M16.1: 风格词与外貌词权重分离 — LLM 画风子句结构改造。

    背景：core E2E（pipeline-7470e3e104d9）国漫任务中，旧子句强制 LLM 将 KB 整串
    风格关键词（含 elaborate costumes / vibrant colors / fantasy elements 等内容词）
    写入每条提示词，与「黑色齐肩短发 / 深蓝色校服」等外貌描述争权重，
    定妆照产出银灰发。分离后：必填仅风格名，KB 整串降为可选氛围参考，
    并显式声明外貌描述权重高于风格氛围词。
    """

    def test_mandatory_line_narrows_to_style_name(self):
        """必填行仅要求风格名，不得携带 KB 内容词（防止 LLM 视作强制注入）。"""
        anchor = resolve_style_anchor("国漫")
        clause = style_prompt_clause(anchor, target="角色定妆照")
        mandatory = clause.split("\n")[0]
        assert "必须显式包含" in mandatory
        assert '"Chinese anime guoman style"' in mandatory
        assert "elaborate costumes" not in mandatory
        assert "vibrant colors" not in mandatory

    def test_full_keywords_demoted_to_optional(self):
        """KB 整串关键词降级为可选氛围参考，不再强制全量注入。"""
        anchor = resolve_style_anchor("国漫")
        clause = style_prompt_clause(anchor, target="角色定妆照")
        optional_line = next(
            line for line in clause.split("\n") if "elaborate costumes" in line
        )
        assert "可选" in optional_line

    def test_appearance_priority_rule_present(self):
        """权重分离规则：外貌描述（发色/服装）优先，冲突氛围词必须舍弃。"""
        anchor = resolve_style_anchor("国漫")
        clause = style_prompt_clause(anchor, target="角色定妆照")
        assert "权重分离规则" in clause
        assert "发色" in clause
        assert "服装" in clause
        assert "必须舍弃" in clause

    def test_target_interpolated(self):
        """target 参数注入子句首行（角色定妆照 / 分镜画面 / 全剧画面）。"""
        anchor = resolve_style_anchor("写实电影感")
        assert "角色定妆照" in style_prompt_clause(anchor, target="角色定妆照")
        assert "分镜画面" in style_prompt_clause(anchor, target="分镜画面")
        assert "全剧画面" in style_prompt_clause(anchor, target="全剧画面")

    def test_optional_line_omitted_when_keywords_equal_style_name(self):
        """keywords_en 与风格名相同（或为空）时不产生可选参考行，权重规则仍保留。"""
        anchor = StyleAnchor(
            key="k",
            title="t",
            keywords_en="some style",
            style_name_en="some style",
            negative_en="",
            is_realistic=False,
        )
        clause = style_prompt_clause(anchor, target="角色定妆照")
        assert "可选" not in clause
        assert "权重分离规则" in clause

    def test_realistic_style_clause(self):
        """写实画风：必填行含写实风格名与中文画风名。"""
        anchor = resolve_style_anchor("写实电影感")
        clause = style_prompt_clause(anchor, target="分镜画面")
        mandatory = clause.split("\n")[0]
        assert '"cinematic realistic"' in mandatory
        assert "「写实电影感」" in mandatory


class TestStripKbAtmosphere:
    """M16.2a: strip_kb_atmosphere 确定性剥离 KB 氛围填充词。

    背景：core E2E（pipeline-87d6d5791120）分镜 prompt 中角色外貌描述完全正确，
    但多角色长 prompt 下 KB 氛围词（elaborate costumes / fantasy elements 等）
    稀释 CLIP 注意力并与锁定外貌冲突，animagineXL40 产出模型先验校服。
    M16.1 已将整串降为「可选」，但 LLM 仍习惯性全量注入，需程序确定性剥离。
    """

    def test_non_realistic_strips_filler_keeps_style_name_and_content(self):
        """目标国漫 → 剥离全部氛围填充词，保留风格名与外貌/场景实质内容。"""
        anchor = resolve_style_anchor("国漫")
        text = (
            "medium shot, a girl with black straight long hair reaching waist, "
            "white shirt, dark gray pleated skirt, Chinese anime guoman style, "
            "vibrant colors, detailed line art, dramatic expressions, "
            "elaborate costumes, standing in classroom"
        )
        cleaned = strip_kb_atmosphere(text, anchor)
        for filler in (
            "vibrant colors",
            "detailed line art",
            "dramatic expressions",
            "elaborate costumes",
        ):
            assert filler not in cleaned
        assert "Chinese anime guoman style" in cleaned
        assert "black straight long hair reaching waist" in cleaned
        assert "white shirt" in cleaned
        assert "dark gray pleated skirt" in cleaned
        assert "standing in classroom" in cleaned
        assert "medium shot" in cleaned

    def test_all_guoman_filler_segments_stripped(self):
        """国漫 KB 条目全部 9 个氛围分段均可命中。"""
        anchor = resolve_style_anchor("国漫")
        text = (
            "portrait, vibrant colors, detailed line art, dramatic expressions, "
            "dynamic poses, fantasy elements, elaborate costumes, particle effects, "
            "cinematic composition, high contrast shading"
        )
        cleaned = strip_kb_atmosphere(text, anchor)
        assert cleaned == "portrait"

    def test_case_insensitive_strip(self):
        anchor = resolve_style_anchor("国漫")
        cleaned = strip_kb_atmosphere(
            "portrait, Vibrant Colors, FANTASY ELEMENTS, soft light", anchor
        )
        assert "Vibrant Colors" not in cleaned
        assert "FANTASY ELEMENTS" not in cleaned
        assert "soft light" in cleaned

    def test_realistic_style_passthrough(self):
        """写实画风不剥离：KB 词为摄影技术词（film grain 等），不与外貌争权重。"""
        anchor = resolve_style_anchor("写实电影感")
        text = "a man in suit, film grain, natural lighting, shallow depth of field"
        assert strip_kb_atmosphere(text, anchor) == text

    def test_empty_and_blank_passthrough(self):
        anchor = resolve_style_anchor("国漫")
        assert strip_kb_atmosphere("", anchor) == ""
        assert strip_kb_atmosphere("   ", anchor) == "   "

    def test_no_filler_when_keywords_equal_style_name(self):
        """keywords_en 仅含风格名时无填充词可剥，原样返回。"""
        anchor = StyleAnchor(
            key="k",
            title="t",
            keywords_en="some style",
            style_name_en="some style",
            negative_en="",
            is_realistic=False,
        )
        text = "portrait, some style, soft light"
        assert strip_kb_atmosphere(text, anchor) == text

    def test_punctuation_cleanup(self):
        """剥离后残留标点应收口：无连续逗号、无首尾逗号。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = strip_kb_atmosphere("vibrant colors, coffee cup,fantasy elements", anchor)
        assert cleaned == "coffee cup"

    def test_word_boundary_protects_similar_words(self):
        """词边界：particles 不得被填充词 particle effects 误伤。"""
        anchor = resolve_style_anchor("国漫")
        cleaned = strip_kb_atmosphere(
            "particle effects, particles floating in air", anchor
        )
        assert cleaned == "particles floating in air"
