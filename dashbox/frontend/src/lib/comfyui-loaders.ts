// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * ComfyUI API Format workflow 中的 loader 节点模型文件引用提取（TS 版）。
 * 与后端 novelvideo.model_library.LOADER_FILE_FIELDS 同步维护。
 */

/** loader class_type → [(文件名字段, 候选模型子目录)] */
export const LOADER_FILE_FIELDS: Record<string, Array<[string, string[]]>> = {
  CheckpointLoaderSimple: [["ckpt_name", ["checkpoints"]]],
  LoraLoader: [["lora_name", ["loras"]]],
  LoraLoaderModelOnly: [["lora_name", ["loras"]]],
  VAELoader: [["vae_name", ["vae"]]],
  LTXVAudioVAELoader: [["vae_name", ["vae"]]],
  CLIPLoader: [["clip_name", ["clip"]]],
  DualCLIPLoader: [["clip_name", ["clip"]]],
  CLIPVisionLoader: [["clip_name", ["clip_vision"]]],
  UNETLoader: [["unet_name", ["unet", "diffusion_models"]]],
  LTXAVTextEncoderLoader: [
    ["text_encoder", ["text_encoders"]],
    ["ckpt_name", ["checkpoints"]],
  ],
  IPAdapterModelLoader: [["ipadapter_file", ["ipadapter"]]],
  UpscaleModelLoader: [["model_name", ["upscale_models"]]],
  LatentUpscaleModelLoader: [["model_name", ["upscale_models"]]],
  ControlNetLoader: [["control_net_name", ["controlnet"]]],
};

export interface WorkflowModelRef {
  nodeId: string;
  classType: string;
  field: string;
  filename: string;
  expectedTypes: string[];
}

interface WorkflowNode {
  class_type?: unknown;
  inputs?: unknown;
}

/** 从 workflow JSON 提取模型文件引用（容忍非法节点，跳过） */
export function extractModelRefs(
  workflow: Record<string, unknown>,
): WorkflowModelRef[] {
  const refs: WorkflowModelRef[] = [];
  for (const [nodeId, rawNode] of Object.entries(workflow)) {
    if (!rawNode || typeof rawNode !== "object" || Array.isArray(rawNode))
      continue;
    const node = rawNode as WorkflowNode;
    const classType = String(node.class_type ?? "");
    const fields = LOADER_FILE_FIELDS[classType];
    if (!fields || !node.inputs || typeof node.inputs !== "object") continue;
    const inputs = node.inputs as Record<string, unknown>;
    for (const [field, expectedTypes] of fields) {
      const value = inputs[field];
      if (typeof value === "string" && value.trim()) {
        refs.push({
          nodeId,
          classType,
          field,
          filename: value.trim(),
          expectedTypes,
        });
      }
    }
  }
  return refs;
}

/** 将 refs 的文件名写回 workflow（返回新对象，不修改入参） */
export function applyModelRef(
  workflow: Record<string, unknown>,
  ref: WorkflowModelRef,
  filename: string,
): Record<string, unknown> {
  const next = { ...workflow };
  const rawNode = next[ref.nodeId];
  if (!rawNode || typeof rawNode !== "object") return next;
  const node = rawNode as { inputs?: Record<string, unknown> };
  next[ref.nodeId] = {
    ...node,
    inputs: { ...(node.inputs ?? {}), [ref.field]: filename },
  };
  return next;
}
