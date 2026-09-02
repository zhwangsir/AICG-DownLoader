import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
  type OnConnect,
  type NodeTypes,
  useStore,
  useReactFlow,
  BackgroundVariant,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  generateScript,
  generateCharacter,
  generateStoryboard,
  generateStoryboardBatch,
  generateVideoAsync,
  generateVideoBatch,
  generateVoice,
  generateSubtitle,
  composeVideo,
  checkQuality,
  checkVisualQuality,
  pollVideoTask,
  type CharacterData,
  type CharacterCardData,
  type SceneData,
  type StoryboardData,
  type VideoData,
  type VoiceData,
  type SubtitleData,
  type EditData,
  type QualityCheckData,
  type QualityVisualData,
  type EditSegmentInput,
} from "../api/client";
import { useDramaStore } from "../store/useDramaStore";
import CharacterPreviewPanel from "./CharacterPreviewPanel";
import NodeDetailPanel, { type ScriptGenerateOptions } from "./NodeDetailPanel";
import DramaNode from "./canvas/DramaNode";
import { getLayoutedElements, type DramaNodeData } from "./canvas/layout";
import { Image as ImageIcon, Film, Sparkles } from "lucide-react";

const nodeTypes: NodeTypes = {
  custom: DramaNode,
};

/**
 * 节点内部数据同步器：在 rAF 被节流的环境（后台标签页/自动化浏览器）中，
 * React Flow v11 的 ResizeObserver 测量链路失效，handleBounds 永不写入导致边不渲染。
 * store 的 updateNodeDimensions 同步读取 DOM 节点与 handle 位置并写入 store，
 * 不依赖 rAF，因此在节点集合变化后主动触发一次，保证边在任何环境下都能渲染。
 */
function NodeInternalsUpdater({ nodesKey }: { nodesKey: string }) {
  const updateNodeDimensions = useStore((s) => s.updateNodeDimensions);
  const instance = useReactFlow();
  const prevCountRef = useRef<number | null>(null);
  const hasInitiallyFitRef = useRef(false);
  useEffect(() => {
    if (!nodesKey) return;
    const timer = setTimeout(() => {
      const ids = nodesKey.split(",");
      const updates = ids
        .map((id) => {
          const el = document.querySelector(
            `.react-flow__node[data-id="${id}"]`
          );
          return el
            ? { id, nodeElement: el as HTMLDivElement, forceUpdate: true }
            : null;
        })
        .filter(
          (u): u is { id: string; nodeElement: HTMLDivElement; forceUpdate: boolean } =>
            !!u
        );
      if (updates.length) updateNodeDimensions(updates);
      const shouldFit = !hasInitiallyFitRef.current || 
        (prevCountRef.current !== null && prevCountRef.current !== ids.length);
      if (shouldFit) {
        requestAnimationFrame(() => {
          instance.fitView({ padding: 0.06, maxZoom: 0.85, minZoom: 0.35, duration: 600 });
        });
        hasInitiallyFitRef.current = true;
      }
      prevCountRef.current = ids.length;
    }, 80);
    return () => clearTimeout(timer);
  }, [nodesKey, updateNodeDimensions, instance]);
  return null;
}

export default function Canvas() {
  const scriptData = useDramaStore((s) => s.scriptData);
  const storyboards = useDramaStore((s) => s.storyboards);
  const videos = useDramaStore((s) => s.videos);
  const voices = useDramaStore((s) => s.voices);
  const subtitles = useDramaStore((s) => s.subtitles);
  const editData = useDramaStore((s) => s.editData);
  const qualityData = useDramaStore((s) => s.qualityData);
  const visualQualityData = useDramaStore((s) => s.visualQualityData);
  const setScriptData = useDramaStore((s) => s.setScriptData);
  const addStoryboard = useDramaStore((s) => s.addStoryboard);
  const addVideo = useDramaStore((s) => s.addVideo);
  const addVoice = useDramaStore((s) => s.addVoice);
  const addSubtitle = useDramaStore((s) => s.addSubtitle);
  const setEditData = useDramaStore((s) => s.setEditData);
  const setQualityData = useDramaStore((s) => s.setQualityData);
  const setVisualQualityData = useDramaStore((s) => s.setVisualQualityData);
  const setStatusInfo = useDramaStore((s) => s.setStatusInfo);
  const projectStyle = useDramaStore((s) => s.projectStyle);
  const setProjectStyle = useDramaStore((s) => s.setProjectStyle);
  const globalLoading = useDramaStore((s) => s.globalLoading);
  const startGlobalLoading = useDramaStore((s) => s.startGlobalLoading);
  const stopGlobalLoading = useDramaStore((s) => s.stopGlobalLoading);

  const [activePreviewCharacterId, setActivePreviewCharacterId] = useState<string | null>(null);
  const [activeDetailNode, setActiveDetailNode] = useState<{
    id: string;
    type: string;
    onGenerate?: (options: ScriptGenerateOptions) => void;
  } | null>(null);

  const [nodes, setNodes] = useState<Node<DramaNodeData>[]>([
    {
      id: "start",
      type: "custom",
      position: { x: 100, y: 200 },
      data: {
        label: "创意输入",
        type: "script",
        detail: "输入一句话创意，一键生成剧本",
        isScriptInput: true,
        generateLabel: "生成剧本",
      },
    },
  ]);
  const [edges, setEdges] = useState<Edge[]>([]);

  const [loadingMap, setLoadingMap] = useState<
    Record<string, { loading: boolean; text: string }>
  >({});

  const canvasContainerRef = useRef<HTMLDivElement>(null);

  const handleCanvasMouseMove = useCallback((e: ReactMouseEvent<HTMLDivElement>) => {
    const el = canvasContainerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    el.style.setProperty("--mx", `${x}%`);
    el.style.setProperty("--my", `${y}%`);
  }, []);

  const characterCards = useDramaStore((s) => s.characterCards);
  const addCharacterCard = useDramaStore((s) => s.addCharacterCard);
  const characterCardImages = useMemo(
    () =>
      Object.fromEntries(
        characterCards.map((c) => {
          const imgs = c.reference_images || {};
          const firstUrl = imgs.front || imgs.portrait || Object.values(imgs)[0] || "";
          return [c.character_id, firstUrl];
        })
      ),
    [characterCards]
  );
  const characterPrompts = useMemo(
    () =>
      Object.fromEntries(
        characterCards
          .filter((c) => c.used_prompts)
          .map((c) => [
            c.character_id,
            {
              positive: c.used_prompts!.positive_prompt,
              negative: c.used_prompts!.negative_prompt,
            },
          ])
      ),
    [characterCards]
  );

  const setLoading = useCallback((id: string, text: string) => {
    setLoadingMap((prev) => ({ ...prev, [id]: { loading: true, text } }));
  }, []);

  const clearLoading = useCallback((id: string) => {
    setLoadingMap((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  );

  const onEdgesChange: OnEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  );

  const onConnect: OnConnect = useCallback(
    (connection) => setEdges((eds) => addEdge(connection, eds)),
    []
  );

  const handleNodeClick = useCallback(
    (_: ReactMouseEvent, node: Node<DramaNodeData>) => {
      if (node.data.type === "character" && node.id.startsWith("char-")) {
        setActivePreviewCharacterId(node.id.replace("char-", ""));
        return;
      }
      setActiveDetailNode({
        id: node.id,
        type: node.data.type,
        onGenerate: node.data.onGenerate,
      });
    },
    []
  );

  const handleGenerateAllStoryboards = useCallback(async () => {
    if (globalLoading || !scriptData) return;
    const pending = scriptData.scenes.filter(
      (s) => !storyboards.some((sb) => sb.scene_id === s.scene_id)
    );
    if (pending.length === 0) {
      setStatusInfo("所有分镜已生成");
      return;
    }
    const taskId = `batch-storyboard-${Date.now()}`;
    const store = useDramaStore.getState();
    store.upsertTask({
      id: taskId,
      label: `批量分镜（${pending.length} 场景）`,
      kind: "batch",
      status: "running",
      percent: 5,
      message: "多 GPU 并行生成中…",
      startedAt: Date.now(),
    });
    startGlobalLoading(`正在批量生成分镜（${pending.length} 个场景）...`);
    pending.forEach((s) => setLoading(`scene-${s.scene_id}`, "批量生成分镜中..."));
    setStatusInfo(`正在批量生成分镜（${pending.length} 个场景），多 GPU 并行执行...`);
    try {
      const resp = await generateStoryboardBatch({
        scenes: pending,
        characters: scriptData.characters,
        style: projectStyle,
      });
      if (resp.success && resp.data) {
        resp.data.results.forEach((r) => addStoryboard(r));
        const failed = resp.data.failed_scenes;
        setStatusInfo(
          `分镜批量生成完成: ${resp.data.results.length} 成功` +
            (failed.length ? `, ${failed.length} 失败` : "")
        );
        store.patchTask(taskId, {
          status: failed.length === pending.length ? "failed" : "completed",
          percent: 100,
          message: `${resp.data.results.length} 成功` + (failed.length ? `，${failed.length} 失败` : ""),
        });
      } else {
        setStatusInfo(`分镜批量生成失败: ${resp.error || "未知错误"}`);
        store.patchTask(taskId, { status: "failed", percent: 100, error: resp.error || "未知错误" });
      }
    } catch (e) {
      setStatusInfo(`分镜批量生成出错: ${String(e)}`);
      store.patchTask(taskId, { status: "failed", percent: 100, error: String(e) });
    } finally {
      pending.forEach((s) => clearLoading(`scene-${s.scene_id}`));
      stopGlobalLoading();
    }
  }, [
    globalLoading,
    scriptData,
    storyboards,
    setStatusInfo,
    startGlobalLoading,
    stopGlobalLoading,
    setLoading,
    clearLoading,
    addStoryboard,
  ]);

  const handleGenerateAllVideos = useCallback(async () => {
    if (globalLoading || !scriptData) return;
    const pending = scriptData.scenes
      .map((s) => {
        const sb = storyboards.find((x) => x.scene_id === s.scene_id);
        const vd = videos.find((x) => x.scene_id === s.scene_id);
        if (!sb || vd) return null;
        return {
          scene_id: s.scene_id,
          image_url: sb.image_url,
          prompt: s.prompt || sb.prompt_used,
          negative_prompt: s.negative_prompt,
        };
      })
      .filter(
        (x): x is { scene_id: number; image_url: string; prompt: string; negative_prompt: string } =>
          !!x
      );
    if (pending.length === 0) {
      setStatusInfo("所有视频已生成");
      return;
    }
    const taskId = `batch-video-${Date.now()}`;
    const store = useDramaStore.getState();
    store.upsertTask({
      id: taskId,
      label: `批量视频（${pending.length} 场景）`,
      kind: "batch",
      status: "running",
      percent: 5,
      message: "多 GPU 并行生成中…",
      startedAt: Date.now(),
    });
    startGlobalLoading(`正在批量生成视频（${pending.length} 个场景）...`);
    pending.forEach((p) => setLoading(`video-${p.scene_id}`, "批量生成视频中..."));
    setStatusInfo(`正在批量生成视频（${pending.length} 个场景），多 GPU 并行执行...`);
    try {
      const resp = await generateVideoBatch({
        items: pending.map((p) => ({
          scene_id: p.scene_id,
          image_url: p.image_url,
          prompt: p.prompt,
          negative_prompt:
            p.negative_prompt ||
            "blurry, low quality, deformed, ugly, watermark, static",
          duration_seconds: 3,
          preview: false,
          quality: "final",
        })),
      });
      if (resp.success && resp.data) {
        resp.data.results.forEach((r) => addVideo(r));
        const failed = resp.data.failed_scenes;
        setStatusInfo(
          `视频批量生成完成: ${resp.data.results.length} 成功` +
            (failed.length ? `, ${failed.length} 失败` : "")
        );
        store.patchTask(taskId, {
          status: failed.length === pending.length ? "failed" : "completed",
          percent: 100,
          message: `${resp.data.results.length} 成功` + (failed.length ? `，${failed.length} 失败` : ""),
        });
      } else {
        setStatusInfo(`视频批量生成失败: ${resp.error || "未知错误"}`);
        store.patchTask(taskId, { status: "failed", percent: 100, error: resp.error || "未知错误" });
      }
    } catch (e) {
      setStatusInfo(`视频批量生成出错: ${String(e)}`);
      store.patchTask(taskId, { status: "failed", percent: 100, error: String(e) });
    } finally {
      pending.forEach((p) => clearLoading(`video-${p.scene_id}`));
      stopGlobalLoading();
    }
  }, [
    globalLoading,
    scriptData,
    storyboards,
    videos,
    setStatusInfo,
    startGlobalLoading,
    stopGlobalLoading,
    setLoading,
    clearLoading,
    addVideo,
  ]);

  useEffect(() => {
    const handleGenerateScript = async (options?: ScriptGenerateOptions) => {
      if (globalLoading) return;
      if (!options) {
        setStatusInfo("生成参数缺失");
        return;
      }
      const { premise, genre, episodes, scenes_per_episode, style, aspect_ratio } = options;
      if (!premise.trim()) {
        setStatusInfo("请输入创意");
        return;
      }
      if (!genre.trim()) {
        setStatusInfo("请输入题材");
        return;
      }
      if (episodes === "" || scenes_per_episode === "") {
        setStatusInfo("请设置集数与每集分镜数");
        return;
      }
      if (!style) {
        setStatusInfo("请选择视觉风格");
        return;
      }
      if (!aspect_ratio) {
        setStatusInfo("请选择画幅比例");
        return;
      }
      // 同步项目级风格与画幅到 store，供后续角色/分镜/视频生成使用
      setProjectStyle(style);
      startGlobalLoading("正在生成剧本...");
      setLoading("start", "正在生成剧本...");
      setStatusInfo("正在生成剧本...");
      try {
        const resp = await generateScript({
          premise,
          genre,
          episodes: Math.max(1, Math.min(100, episodes)),
          scenes_per_episode: Math.max(1, Math.min(30, scenes_per_episode)),
        });
        if (resp.success && resp.data) {
          setScriptData(resp.data);
          setStatusInfo(
            `剧本已生成: ${resp.data.title} | ${resp.data.characters.length} 角色 | ${resp.data.scenes.length} 分镜`
          );
        } else {
          setStatusInfo(`剧本生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`剧本生成出错: ${String(e)}`);
      } finally {
        clearLoading("start");
        stopGlobalLoading();
      }
    };

    const handleResetScript = () => {
      setScriptData(null);
      setStatusInfo("已重置，请输入新创意");
    };

    const handleGenerateCharacter = (
      char: CharacterData,
      customPositive?: string,
      customNegative?: string
    ) => {
      if (customPositive && customPositive.trim()) {
        generateCharacterDirect(char, customPositive, customNegative || "");
        return;
      }
      setActivePreviewCharacterId(char.character_id);
    };

    const generateCharacterDirect = async (
      char: CharacterData,
      customPositive: string,
      customNegative: string
    ) => {
      if (globalLoading) return;
      const nodeId = `char-${char.character_id}`;
      startGlobalLoading(`正在生成 ${char.name} 的定妆照...`);
      setLoading(nodeId, "生成定妆照中...");
      setStatusInfo(`正在生成角色定妆照: ${char.name}...`);
      try {
        const resp = await generateCharacter({
          character: char,
          style: projectStyle,
          consistency_level: "L3",
          custom_positive_prompt: customPositive,
          custom_negative_prompt: customNegative,
        });
        if (resp.success && resp.data) {
          applyCharacterResult(char.character_id, resp.data);
          setStatusInfo(`角色定妆照已生成: ${char.name}`);
        } else {
          setStatusInfo(`定妆照生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`定妆照生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const applyCharacterResult = (charId: string, data: CharacterCardData) => {
      addCharacterCard(data);
    };

    const handleGenerateStoryboard = async (scene: SceneData) => {
      if (globalLoading) return;
      const nodeId = `scene-${scene.scene_id}`;
      startGlobalLoading(`正在生成分镜: 场景 ${scene.scene_id}...`);
      setLoading(nodeId, "生成分镜中...");
      setStatusInfo(`正在生成分镜: 场景 ${scene.scene_id}...`);
      try {
        const resp = await generateStoryboard({
          scene,
          characters: scriptData?.characters || [],
          style: projectStyle,
        });
        if (resp.success && resp.data) {
          addStoryboard(resp.data);
          setStatusInfo(`分镜关键帧已生成: 场景 ${scene.scene_id}`);
        } else {
          setStatusInfo(`分镜生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`分镜生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleGenerateVideo = async (
      sceneId: number,
      imageUrl: string,
      prompt: string,
      negativePrompt: string
    ) => {
      if (globalLoading) return;
      const nodeId = `video-${sceneId}`;
      const taskId = `video-${sceneId}-${Date.now()}`;
      const store = useDramaStore.getState();
      store.upsertTask({
        id: taskId,
        label: `视频：场景 ${sceneId}`,
        kind: "video",
        status: "running",
        percent: 0,
        message: "任务创建中…",
        startedAt: Date.now(),
      });
      startGlobalLoading(`正在生成视频: 场景 ${sceneId}...`);
      setLoading(nodeId, "生成视频中...");
      setStatusInfo(`正在生成视频: 场景 ${sceneId}...`);
      try {
        const task = await generateVideoAsync({
          scene_id: sceneId,
          image_url: imageUrl,
          prompt,
          negative_prompt:
            negativePrompt ||
            "blurry, low quality, deformed, ugly, watermark, static",
          duration_seconds: 3,
          preview: false,
          quality: "final",
        });
        const evt = await pollVideoTask(task.poll_url, undefined, (p) => {
          store.patchTask(taskId, {
            percent: p.percent,
            message: p.message || "生成中…",
          });
        });
        if (
          evt.status === "completed" &&
          evt.result &&
          typeof evt.result === "object" &&
          "video_url" in evt.result
        ) {
          const vd = evt.result as VideoData;
          addVideo(vd);
          setStatusInfo(
            `视频已生成: 场景 ${sceneId} (${vd.duration_seconds}s)`
          );
          store.patchTask(taskId, { status: "completed", percent: 100, message: `${vd.duration_seconds}s 成片` });
        } else {
          setStatusInfo(
            `视频生成失败: ${evt.error || "未知错误"}`
          );
          store.patchTask(taskId, { status: "failed", percent: 100, error: evt.error || "未知错误" });
        }
      } catch (e) {
        setStatusInfo(`视频生成出错: ${String(e)}`);
        store.patchTask(taskId, { status: "failed", percent: 100, error: String(e) });
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleGenerateVoice = async (scene: SceneData) => {
      if (globalLoading) return;
      const nodeId = `voice-${scene.scene_id}`;
      if (!scene.dialogue) {
        setStatusInfo(`场景 ${scene.scene_id} 没有台词，无法生成配音`);
        return;
      }
      startGlobalLoading(`正在生成配音: 场景 ${scene.scene_id}...`);
      setLoading(nodeId, "生成配音中...");
      setStatusInfo(`正在生成配音: 场景 ${scene.scene_id}...`);
      try {
        const chars = scriptData?.characters || [];
        const lines = scene.dialogue
          .split(/[，。！？\n]/)
          .map((t) => t.trim())
          .filter((t) => t.length > 1);
        const dialogues = lines.map((text, i) => {
          const speaker = chars[i % Math.max(chars.length, 1)];
          return {
            text,
            character_name: speaker?.name || `角色${i + 1}`,
            character_role: speaker?.role || "",
            character_age: speaker?.age ?? null,
            rate: "+0%",
          };
        });
        const resp = await generateVoice({
          scene_id: scene.scene_id,
          dialogues,
        });
        if (resp.success && resp.data) {
          addVoice(resp.data);
          setStatusInfo(
            `配音已生成: 场景 ${scene.scene_id} (${resp.data.total_lines} 条语音)`
          );
        } else {
          setStatusInfo(`配音生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`配音生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleGenerateSubtitle = async (
      sceneId: number,
      audioUrl: string
    ) => {
      if (globalLoading) return;
      const nodeId = `subtitle-${sceneId}`;
      startGlobalLoading(`正在生成字幕: 场景 ${sceneId}...`);
      setLoading(nodeId, "生成字幕中...");
      setStatusInfo(`正在生成字幕: 场景 ${sceneId}...`);
      try {
        const resp = await generateSubtitle({
          scene_id: sceneId,
          audio_url: audioUrl,
          language: "zh",
        });
        if (resp.success && resp.data) {
          addSubtitle(resp.data);
          setStatusInfo(
            `字幕已生成: 场景 ${sceneId} (${resp.data.segments.length} 段)`
          );
        } else {
          setStatusInfo(`字幕生成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`字幕生成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleComposeVideo = async () => {
      if (globalLoading) return;
      const nodeId = "edit-final";
      const readyScenes: EditSegmentInput[] = videos
        .map((v) => {
          const voice = voices.find((vo) => vo.scene_id === v.scene_id);
          const subtitle = subtitles.find((s) => s.scene_id === v.scene_id);
          if (!voice || voice.audio_urls.length === 0 || !subtitle) return null;
          return {
            scene_id: v.scene_id,
            video_url: v.video_url,
            audio_url: voice.audio_urls[0].audio_url,
            subtitle_url: subtitle.srt_url,
          };
        })
        .filter((s): s is EditSegmentInput => s !== null);

      if (readyScenes.length === 0) {
        setStatusInfo("没有完整素材的场景（需视频+配音+字幕）");
        return;
      }
      startGlobalLoading(`正在合成成片（${readyScenes.length} 个场景）...`);
      setLoading(nodeId, "合成成片中...");
      setStatusInfo(`正在合成成片（${readyScenes.length} 个场景）...`);
      try {
        const resp = await composeVideo({
          project_id: scriptData?.project_id || `project-${Date.now()}`,
          title: scriptData?.title || "未命名短剧",
          segments: readyScenes,
          transition: "fade",
          bgm_url: null,
          output_resolution: "1080x1920",
          output_fps: 30,
        });
        if (resp.success && resp.data) {
          setEditData(resp.data);
          setStatusInfo(
            `成片已合成: ${resp.data.title} | ${resp.data.segments_count} 场景 | ${resp.data.duration_seconds.toFixed(1)}s`
          );
        } else {
          setStatusInfo(`合成失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`合成出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleCheckQuality = async () => {
      if (globalLoading) return;
      const nodeId = "quality-final";
      if (!scriptData) return;
      startGlobalLoading("正在执行剧本质检...");
      setLoading(nodeId, "质检中...");
      setStatusInfo("正在执行剧本质检...");
      try {
        const resp = await checkQuality({
          project_id: scriptData.project_id || `project-${Date.now()}`,
          title: scriptData.title,
          characters: scriptData.characters,
          scenes: scriptData.scenes,
          subtitles,
        });
        if (resp.success && resp.data) {
          setQualityData(resp.data);
          const critical = resp.data.issues.filter(
            (i) => i.severity === "critical"
          ).length;
          setStatusInfo(
            `质检完成: 质量分 ${resp.data.score} | ${resp.data.issues.length} 问题 | ${critical} 严重`
          );
        } else {
          setStatusInfo(`质检失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`质检出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const handleCheckVisualQuality = async () => {
      if (globalLoading) return;
      const nodeId = "visual-quality-final";
      const targetVideo = videos[0];
      if (!targetVideo) return;
      startGlobalLoading(`正在执行视觉质检: 场景 ${targetVideo.scene_id}...`);
      setLoading(nodeId, "视觉质检中...");
      setStatusInfo(`正在执行视觉质检: 场景 ${targetVideo.scene_id}...`);
      try {
        const resp = await checkVisualQuality({
          project_id: scriptData?.project_id || `project-${Date.now()}`,
          title: scriptData?.title || "未命名短剧",
          scene_id: targetVideo.scene_id,
          video_url: targetVideo.video_url,
          max_frames: 6,
        });
        if (resp.success && resp.data) {
          setVisualQualityData(resp.data);
          const critical = resp.data.issues.filter(
            (i) => i.severity === "critical"
          ).length;
          setStatusInfo(
            `视觉质检完成: 场景 ${resp.data.scene_id} | 质量分 ${resp.data.score} | ${critical} 严重`
          );
        } else {
          setStatusInfo(`视觉质检失败: ${resp.error || "未知错误"}`);
        }
      } catch (e) {
        setStatusInfo(`视觉质检出错: ${String(e)}`);
      } finally {
        clearLoading(nodeId);
        stopGlobalLoading();
      }
    };

    const loadingFor = (id: string) => loadingMap[id]?.loading || false;
    const loadingTextFor = (id: string) => loadingMap[id]?.text || "";

    const newNodes: Node<DramaNodeData>[] = [
      {
        id: "start",
        type: "custom",
        position: { x: 100, y: 200 },
        data: scriptData
          ? {
              label: "重新创作",
              type: "script",
              detail: "点击重置并输入新的创意",
              isEditInput: true,
              hasGenerated: false,
              loading: loadingFor("start"),
              loadingText: loadingTextFor("start"),
              onGenerate: handleResetScript,
              onOpenDetail: () =>
                setActiveDetailNode({
                  id: "start",
                  type: "script",
                  onGenerate: handleResetScript,
                }),
            }
          : {
              label: "创意输入",
              type: "script",
              detail: "输入一句话创意，一键生成剧本",
              isScriptInput: true,
              hasGenerated: false,
              loading: loadingFor("start"),
              loadingText: loadingTextFor("start"),
              onGenerate: handleGenerateScript,
              onOpenDetail: () =>
                setActiveDetailNode({
                  id: "start",
                  type: "script",
                  onGenerate: handleGenerateScript,
                }),
            },
      },
    ];

    const newEdges: Edge[] = [];

    const addEdgeWithAnim = (
      id: string,
      source: string,
      target: string,
      handles?: { sourceHandle?: string; targetHandle?: string }
    ) => {
      const isFlowing = loadingFor(target);
      newEdges.push({
        id,
        source,
        target,
        ...handles,
        animated: isFlowing,
        type: "smoothstep",
      });
    };

    if (scriptData) {
      newNodes.push({
        id: "script",
        type: "custom",
        position: { x: 400, y: 100 },
        data: {
          label: `剧本: ${scriptData.title}`,
          type: "script",
          detail: `${scriptData.total_episodes} 集 | ${scriptData.scenes.length} 分镜 | ${scriptData.characters.length} 角色`,
          preview: scriptData.genre
            ? `题材：${scriptData.genre}。${scriptData.scenes[0]?.description || ""}`
            : scriptData.scenes[0]?.description,
          tags: [
            scriptData.genre,
            `${scriptData.total_episodes} 集`,
            `${scriptData.scenes.length} 分镜`,
            `${scriptData.characters.length} 角色`,
          ].filter(Boolean) as string[],
          meta: [
            { label: "题材", value: scriptData.genre || "—" },
            { label: "场景数", value: String(scriptData.scenes.length) },
            { label: "角色数", value: String(scriptData.characters.length) },
            { label: "集数", value: String(scriptData.total_episodes) },
          ],
          hasGenerated: true,

        },
      });
      addEdgeWithAnim("e-start-script", "start", "script");

      scriptData.characters.forEach((char) => {
        const charId = `char-${char.character_id}`;
        const img = characterCardImages[char.character_id];
        const prompts = characterPrompts[char.character_id];
        newNodes.push({
          id: charId,
          type: "custom",
          position: { x: 700, y: 100 },
          data: {
            label: `角色: ${char.name}`,
            type: "character",
            detail: char.description || `${char.role} · ${char.age ? `${char.age}岁` : ""}`,
            preview: char.personality
              ? `${char.personality}。${char.description}`
              : char.description,
            tags: [char.role, char.age ? `${char.age}岁` : undefined].filter(Boolean) as string[],
            meta: [
              { label: "身份", value: char.role || "—" },
              { label: "年龄", value: char.age ? `${char.age}岁` : "—" },
              { label: "状态", value: img ? "定妆照已生成" : "待生成定妆照" },
            ],
            imageUrl: img,
            hasGenerated: !!img,
            statusText: img ? "定妆照已生成" : "待生成定妆照",
            generateLabel: img ? "重新生成定妆照" : "生成定妆照",
            showAgentAssist: true,
            loading: loadingFor(charId),
            loadingText: loadingTextFor(charId),
            onGenerate: () => handleGenerateCharacter(char),
            ...(prompts
              ? {
                  editablePrompts: prompts,
                  onEditPrompts: (positive: string, negative: string) =>
                    handleGenerateCharacter(char, positive, negative),
                }
              : {}),

          },
        });
        addEdgeWithAnim(`e-script-${charId}`, "script", charId);
      });

      const storyboardMap = new Map(storyboards.map((s) => [s.scene_id, s]));
      const videoMap = new Map(videos.map((v) => [v.scene_id, v]));
      const voiceMap = new Map(voices.map((v) => [v.scene_id, v]));
      const subtitleMap = new Map(subtitles.map((s) => [s.scene_id, s]));

      const allCharactersHaveImages = scriptData.characters.every(
        (c) => !!characterCardImages[c.character_id]
      );

      scriptData.scenes.slice(0, 3).forEach((scene) => {
        const sb = storyboardMap.get(scene.scene_id);
        const vd = videoMap.get(scene.scene_id);
        const vc = voiceMap.get(scene.scene_id);
        const st = subtitleMap.get(scene.scene_id);
        const hasVoiceAudio = !!(vc && vc.audio_urls.length > 0);

        const sceneId = `scene-${scene.scene_id}`;
        newNodes.push({
          id: sceneId,
          type: "custom",
          position: { x: 1000, y: 100 },
          data: {
            label: `分镜 ${scene.scene_id}: ${scene.shot_type}`,
            type: "storyboard",
            detail:
              scene.description.length > 60
                ? scene.description.slice(0, 60) + "…"
                : scene.description,
            preview: scene.dialogue
              ? `「${scene.dialogue}」${scene.character_actions ? ` · ${scene.character_actions}` : ""}`
              : scene.character_actions
              ? scene.character_actions
              : scene.description,
            tags: [
              scene.shot_type,
              scene.emotion,
              scene.camera_movement,
              `${scene.duration_seconds}s`,
            ].filter(Boolean) as string[],
            imageUrl: sb?.image_url,
            hasGenerated: !!sb,
            statusText: sb ? "分镜图已生成" : "待生成分镜图",
            generateLabel: sb ? "重新生成分镜" : "生成分镜",
            canGenerate: allCharactersHaveImages,
            lockReason: allCharactersHaveImages
              ? undefined
              : "请先生成所有角色定妆照",
            loading: loadingFor(sceneId),
            loadingText: loadingTextFor(sceneId),
            onGenerate: () => handleGenerateStoryboard(scene),
          },
        });
        addEdgeWithAnim(`e-script-${sceneId}`, "script", sceneId);

        // 视频节点：始终展示，未生成时显示预览与锁定原因
        const videoNodeId = `video-${scene.scene_id}`;
        newNodes.push({
          id: videoNodeId,
          type: "custom",
          position: { x: 1300, y: 100 },
          data: {
            label: `视频 ${scene.scene_id}`,
            type: "video",
            detail: vd
              ? `已生成 (${vd.duration_seconds}s)`
              : `MiniMax-H3 · ${scene.duration_seconds}s`,
            preview: vd
              ? `分辨率 768×1344 · ${vd.duration_seconds || scene.duration_seconds}s`
              : sb
              ? `基于分镜图生成 ${scene.duration_seconds}s 视频：${(scene.description || "").slice(0, 60)}${(scene.description || "").length > 60 ? "…" : ""}`
              : `基于分镜图生成 ${scene.duration_seconds}s 视频：${(scene.description || "").slice(0, 60)}${(scene.description || "").length > 60 ? "…" : ""}`,
            tags: ["MiniMax-H3", `${scene.duration_seconds || 3}s`].filter(Boolean) as string[],
            meta: [
              { label: "模型", value: "MiniMax-H3" },
              { label: "时长", value: `${vd?.duration_seconds || scene.duration_seconds || 0}s` },
              { label: "分辨率", value: "768×1344" },
              { label: "状态", value: vd ? "已生成" : "待生成" },
            ],
            videoUrl: vd?.video_url,
            hasGenerated: !!vd,
            statusText: vd ? "视频已生成" : "待生成视频",
            generateLabel: vd ? "重新生成视频" : "生成视频",
            canGenerate: !!sb,
            lockReason: sb ? undefined : "请先生成该场景分镜图",
            loading: loadingFor(videoNodeId),
            loadingText: loadingTextFor(videoNodeId),
            onGenerate: sb
              ? () =>
                  handleGenerateVideo(
                    scene.scene_id,
                    sb.image_url,
                    scene.prompt || sb.prompt_used,
                    scene.negative_prompt
                  )
              : undefined,
          },
        });
        addEdgeWithAnim(`e-${sceneId}-${videoNodeId}`, sceneId, videoNodeId);

        // 配音节点：始终展示
        const voiceNodeId = `voice-${scene.scene_id}`;
        newNodes.push({
          id: voiceNodeId,
          type: "custom",
          position: { x: 1300, y: 320 },
          data: {
            label: `配音 ${scene.scene_id}`,
            type: "voice",
            detail: vc
              ? `IndexTTS-2 · ${vc.total_lines} 条`
              : "IndexTTS-2 自动提取对白",
            preview: vc
              ? `已生成 ${vc.total_lines} 条语音。${scene.dialogue ? `对白：${scene.dialogue.length > 60 ? scene.dialogue.slice(0, 60) + "…" : scene.dialogue}` : ""}`
              : scene.dialogue
              ? `待生成配音 · 对白：${scene.dialogue.length > 80 ? scene.dialogue.slice(0, 80) + "…" : scene.dialogue}`
              : "待生成后展示对白摘要",
            tags: ["IndexTTS-2", vc ? `${vc.total_lines} 条` : undefined].filter(Boolean) as string[],
            meta: [
              { label: "引擎", value: "IndexTTS-2" },
              { label: "台词数", value: vc ? `${vc.total_lines} 条` : "—" },
              { label: "状态", value: vc ? "已生成" : "待生成" },
            ],
            audioUrl: vc?.audio_urls[0]?.audio_url,
            hasGenerated: !!vc,
            statusText: vc ? "配音已生成" : "待生成配音",
            generateLabel: vc ? "重新生成配音" : "生成配音",
            canGenerate: !!vd,
            lockReason: vd ? undefined : "请先生成该场景视频",
            loading: loadingFor(voiceNodeId),
            loadingText: loadingTextFor(voiceNodeId),
            onGenerate: () => handleGenerateVoice(scene),
          },
        });
        addEdgeWithAnim(`e-${videoNodeId}-${voiceNodeId}`, videoNodeId, voiceNodeId);

        // 字幕节点：始终展示
        const subtitleNodeId = `subtitle-${scene.scene_id}`;
        const subtitlePreview = st
          ? st.segments
              .slice(0, 3)
              .map((seg) => seg.text)
              .join(" / ")
          : scene.dialogue
          ? scene.dialogue.length > 80
            ? scene.dialogue.slice(0, 80) + "…"
            : scene.dialogue
          : "";
        newNodes.push({
          id: subtitleNodeId,
          type: "custom",
          position: { x: 1600, y: 320 },
          data: {
            label: `字幕 ${scene.scene_id}`,
            type: "subtitle",
            detail: st
              ? `faster-whisper (${st.language}) · ${st.segments.length} 段`
              : "faster-whisper ASR",
            preview: subtitlePreview || "待生成后展示字幕片段",
            tags: ["faster-whisper", st ? `${st.segments.length} 段` : undefined, st?.language].filter(Boolean) as string[],
            meta: [
              { label: "引擎", value: "faster-whisper" },
              { label: "语言", value: st?.language || "—" },
              { label: "段数", value: st ? `${st.segments.length} 段` : "—" },
              { label: "状态", value: st ? "已生成" : "待生成" },
            ],
            subtitleText: subtitlePreview,
            hasGenerated: !!st,
            statusText: st ? "字幕已生成" : "待生成字幕",
            generateLabel: st ? "重新生成字幕" : "生成字幕",
            canGenerate: hasVoiceAudio,
            lockReason: hasVoiceAudio ? undefined : "请先生成该场景配音",
            loading: loadingFor(subtitleNodeId),
            loadingText: loadingTextFor(subtitleNodeId),
            onGenerate: hasVoiceAudio
              ? () =>
                  handleGenerateSubtitle(
                    scene.scene_id,
                    vc.audio_urls[0].audio_url
                  )
              : undefined,
          },
        });
        addEdgeWithAnim(`e-${voiceNodeId}-${subtitleNodeId}`, voiceNodeId, subtitleNodeId);
      });

      const allScenesReady = scriptData.scenes.every((s) => {
        const v = videoMap.get(s.scene_id);
        const voice = voiceMap.get(s.scene_id);
        const sub = subtitleMap.get(s.scene_id);
        return (
          !!v && !!voice && voice.audio_urls.length > 0 && !!sub
        );
      });
      const hasAnyVideo = videos.length > 0;
      if (hasAnyVideo || editData) {
        newNodes.push({
          id: "edit-final",
          type: "custom",
          position: { x: 1900, y: 320 },
          data: {
            label: editData ? `成片: ${editData.title}` : "合成成片",
            type: "edit",
            detail: editData
              ? `${editData.segments_count} 场景 | ${editData.duration_seconds.toFixed(1)}s`
              : "合成视频+配音+字幕",
            tags: editData
              ? ["成片", `${editData.segments_count} 场景`, `${editData.duration_seconds.toFixed(1)}s`]
              : ["待合成"],
            meta: editData
              ? [
                  { label: "场景数", value: `${editData.segments_count}` },
                  { label: "总时长", value: `${editData.duration_seconds.toFixed(1)}s` },
                  { label: "分辨率", value: "1080x1920" },
                  { label: "状态", value: "已合成" },
                ]
              : [
                  { label: "输入", value: "视频+配音+字幕" },
                  { label: "状态", value: "待合成" },
                ],
            videoUrl: editData?.final_video_url,
            hasGenerated: !!editData,
            statusText: editData ? "成片已合成" : "待合成成片",
            generateLabel: editData ? "重新合成成片" : "合成成片",
            canGenerate: allScenesReady,
            lockReason: allScenesReady
              ? undefined
              : "请先完成所有场景的视频、配音、字幕",
            loading: loadingFor("edit-final"),
            loadingText: loadingTextFor("edit-final"),
            onGenerate: handleComposeVideo,
          },
        });
        scriptData.scenes.slice(0, 3).forEach((s) => {
          addEdgeWithAnim(
            `e-subtitle-${s.scene_id}-edit`,
            `subtitle-${s.scene_id}`,
            "edit-final"
          );
        });
      }

      // 质检节点：仅在已合成成片或已有质检结果时显示，避免空节点过度扩张画布
      if (editData || qualityData) {
        const qualitySummary = qualityData
          ? `质量分 ${qualityData.score} | ${qualityData.issues.length} 问题`
          : "台词一致性 / 剧情逻辑 / 敏感词";
        const qualityIssuesPreview = qualityData
          ? qualityData.issues
              .slice(0, 3)
              .map(
                (i) => `[${i.severity}] ${i.message}`
              )
              .join("\n")
          : "";
        newNodes.push({
          id: "quality-final",
          type: "custom",
          position: { x: 400, y: 500 },
          data: {
            label: qualityData ? `质检: ${qualityData.title}` : "剧本质检",
            type: "quality",
            detail: qualityData
              ? `已检查 | 质量分 ${qualityData.score}`
              : "一键质检",
            tags: qualityData
              ? [`质量分 ${qualityData.score}`, `${qualityData.issues.length} 问题`]
              : ["剧本 / 字幕"],
            meta: qualityData
              ? [
                  { label: "质量分", value: `${qualityData.score}` },
                  { label: "问题数", value: `${qualityData.issues.length}` },
                  { label: "严重", value: `${qualityData.issues.filter((i) => i.severity === "critical").length}` },
                  { label: "状态", value: "已检查" },
                ]
              : [
                  { label: "检查项", value: "台词 / 逻辑 / 敏感词" },
                  { label: "状态", value: "待检查" },
                ],
            qualitySummary,
            qualityIssues: qualityIssuesPreview,
            hasGenerated: !!qualityData,
            statusText: qualityData ? "质检完成" : "待质检",
            generateLabel: qualityData ? "重新质检" : "一键质检",
            canGenerate: !!editData,
            lockReason: editData ? undefined : "请先合成成片",
            loading: loadingFor("quality-final"),
            loadingText: loadingTextFor("quality-final"),
            onGenerate: handleCheckQuality,
          },
        });
        addEdgeWithAnim("e-script-quality", "script", "quality-final", {
          sourceHandle: "source-bottom",
          targetHandle: "target-top",
        });
      }

      if (videos.length > 0) {
        const vqSummary = visualQualityData
          ? `质量分 ${visualQualityData.score} | 场景 ${visualQualityData.scene_id}`
          : "角色一致性 / 画面连贯性";
        const vqIssuesPreview = visualQualityData
          ? visualQualityData.issues
              .slice(0, 3)
              .map((i) => `[${i.severity}] ${i.message}`)
              .join("\n")
          : "";
        newNodes.push({
          id: "visual-quality-final",
          type: "custom",
          position: { x: 1600, y: 540 },
          data: {
            label: visualQualityData
              ? `视觉质检: 场景 ${visualQualityData.scene_id}`
              : "视觉质检",
            type: "visual_quality",
            detail: visualQualityData
              ? `已检查 | 质量分 ${visualQualityData.score}`
              : "视频画面质检",
            tags: visualQualityData
              ? [`质量分 ${visualQualityData.score}`, `场景 ${visualQualityData.scene_id}`]
              : ["画面 / 一致性"],
            meta: visualQualityData
              ? [
                  { label: "场景", value: `场景 ${visualQualityData.scene_id}` },
                  { label: "质量分", value: `${visualQualityData.score}` },
                  { label: "问题数", value: `${visualQualityData.issues.length}` },
                  { label: "状态", value: "已检查" },
                ]
              : [
                  { label: "检查项", value: "角色 / 连贯性" },
                  { label: "状态", value: "待检查" },
                ],
            qualitySummary: vqSummary,
            qualityIssues: vqIssuesPreview,
            hasGenerated: !!visualQualityData,
            statusText: visualQualityData ? "视觉质检完成" : "待视觉质检",
            generateLabel: visualQualityData ? "重新视觉质检" : "视觉质检",
            loading: loadingFor("visual-quality-final"),
            loadingText: loadingTextFor("visual-quality-final"),
            onGenerate: handleCheckVisualQuality,
          },
        });
        addEdgeWithAnim(
          "e-video-visual-quality",
          `video-${videos[0].scene_id}`,
          "visual-quality-final"
        );
      }
    }

    const lockedNodes = newNodes.map((node) => {
      if (!globalLoading || !node.data.generateLabel) return node;
      return {
        ...node,
        data: {
          ...node.data,
          canGenerate: false,
          lockReason: "有其他生成任务进行中，请等待完成",
        },
      };
    });

    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
      lockedNodes,
      newEdges
    );
    setNodes(layoutedNodes);
    setEdges(layoutedEdges);
  }, [
    scriptData,
    storyboards,
    videos,
    voices,
    subtitles,
    editData,
    qualityData,
    visualQualityData,
    loadingMap,
    characterCards,
    addCharacterCard,
    projectStyle,
    setProjectStyle,
    globalLoading,
    setScriptData,
    addStoryboard,
    addVideo,
    addVoice,
    addSubtitle,
    setEditData,
    setQualityData,
    setVisualQualityData,
    setStatusInfo,
    setLoading,
    clearLoading,
    startGlobalLoading,
    stopGlobalLoading,
  ]);

  const nodesKey = useMemo(() => nodes.map((n) => n.id).join(","), [nodes]);

  return (
    <>
      {activePreviewCharacterId && (
        <CharacterPreviewPanel
          characterId={activePreviewCharacterId}
          onClose={() => setActivePreviewCharacterId(null)}
        />
      )}
      {activeDetailNode && (
        <NodeDetailPanel
          nodeId={activeDetailNode.id}
          type={activeDetailNode.type}
          data={nodes.find((n) => n.id === activeDetailNode.id)?.data}
          onGenerate={activeDetailNode.onGenerate}
          onClose={() => setActiveDetailNode(null)}
        />
      )}

      <div
        className="canvas-container"
        ref={canvasContainerRef}
        onMouseMove={handleCanvasMouseMove}
      >
        <div className="canvas-spotlight" />
        <div className="react-flow-wrapper">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={handleNodeClick}
            nodeTypes={nodeTypes}
            fitView
            fitViewOptions={{ padding: 0.06, maxZoom: 0.85, minZoom: 0.35, duration: 600 }}
            maxZoom={1.2}
            minZoom={0.35}
            defaultEdgeOptions={{
              style: { stroke: "var(--border-medium)", strokeWidth: 2 },
              type: "smoothstep",
              animated: false,
            }}
            proOptions={{ hideAttribution: false }}
          >
            <Background
              variant={BackgroundVariant.Dots}
              color="#c8bca7"
              gap={24}
              size={1.5}
            />
            <Controls showInteractive={false} position="bottom-left" />
            {/* LibTV 画布标配 MiniMap：深色化 + 节点类型色映射 */}
            <MiniMap
              position="top-right"
              pannable
              zoomable
              className="canvas-minimap"
              nodeColor={(n) => {
                const t = (n.data as { type?: string })?.type ?? "script";
                return (
                  {
                    script: "#c9b896",
                    character: "#e08ab8",
                    storyboard: "#09caf5",
                    video: "#5eb8d4",
                    voice: "#7ec98f",
                    subtitle: "#b8a88a",
                    edit: "#f2664d",
                    quality: "#f2a93a",
                    visual_quality: "#a8c46a",
                  } as Record<string, string>
                )[t] ?? "#c9b896";
              }}
              maskColor="rgba(20, 20, 20, 0.72)"
            />
            <NodeInternalsUpdater nodesKey={nodesKey} />
          </ReactFlow>
        </div>

        {/* 空画布引导：无剧本时提示三步上手路径（LibTV Onboarding 式） */}
        {!scriptData && (
          <div className="canvas-onboarding">
            <div className="canvas-onboarding-step">
              <span className="canvas-onboarding-num">1</span>底部输入创意，回车生成剧本
            </div>
            <div className="canvas-onboarding-arrow">→</div>
            <div className="canvas-onboarding-step">
              <span className="canvas-onboarding-num">2</span>角色定妆照锁定外观
            </div>
            <div className="canvas-onboarding-arrow">→</div>
            <div className="canvas-onboarding-step">
              <span className="canvas-onboarding-num">3</span>逐镜分镜 → 视频 → 成片
            </div>
          </div>
        )}

        {/* 批量操作：锚定在画布容器右下，随面板开关闭合自动避让 */}
        <div className="floating-actions">
          <button
            className="floating-btn"
            disabled={globalLoading || !scriptData}
            onClick={handleGenerateAllStoryboards}
          >
            <ImageIcon size={13} strokeWidth={2.2} />
            批量生成分镜
          </button>
          <button
            className="floating-btn"
            disabled={globalLoading || storyboards.length === 0}
            onClick={handleGenerateAllVideos}
          >
            <Film size={13} strokeWidth={2.2} />
            批量生成视频
          </button>
        </div>
      </div>
    </>
  );
}
