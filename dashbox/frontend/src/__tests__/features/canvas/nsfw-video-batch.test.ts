// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/** R18 出片节点纯函数助手测试：帧数换算 / 路线尺寸对齐 / SRT 字幕生成。 */
import { describe, expect, it } from 'vitest';

import {
  buildSrtContent,
  shotLengthFrames,
  shotVideoSize,
} from '@/features/canvas/nodes/NSFWVideoBatchNode';
import type { NsfwVideoBatchShot } from '@/features/canvas/domain/canvasNodes';

describe('shotLengthFrames', () => {
  it('wan 16fps：5s→81、10s→161', () => {
    expect(shotLengthFrames(5, 'wan')).toBe(81);
    expect(shotLengthFrames(10, 'wan')).toBe(161);
  });

  it('h3 ≈24.8fps：5s→124、3s→74', () => {
    expect(shotLengthFrames(5, 'h3')).toBe(124);
    expect(shotLengthFrames(3, 'h3')).toBe(74);
  });

  it('clamp 到 [9,241]：超长镜头 15s 封顶 241', () => {
    expect(shotLengthFrames(15, 'h3')).toBe(241);
    expect(shotLengthFrames(0, 'wan')).toBe(9);
  });
});

describe('shotVideoSize', () => {
  it('h3 竖版钉死 768×1344（忽略上游 832x1216）', () => {
    expect(shotVideoSize('832x1216', 'h3')).toEqual({ width: 768, height: 1344 });
  });

  it('h3 横版钉死 1344×768', () => {
    expect(shotVideoSize('1216x832', 'h3')).toEqual({ width: 1344, height: 768 });
  });

  it('wan 最长边 832：竖版 832x1216 → 576x832（16 对齐，≈原始 2:3）', () => {
    expect(shotVideoSize('832x1216', 'wan')).toEqual({ width: 576, height: 832 });
  });

  it('wan 横版 1216x832 → 832x576', () => {
    expect(shotVideoSize('1216x832', 'wan')).toEqual({ width: 832, height: 576 });
  });
});

describe('buildSrtContent', () => {
  const shot = (over: Partial<NsfwVideoBatchShot>): NsfwVideoBatchShot => ({
    id: 's',
    sceneNo: 1,
    kind: 'plot',
    title: '',
    videoPrompt: '',
    presetId: '',
    dialogue: '',
    narration: '',
    durationSec: 5,
    audio: 'tts',
    firstFrameUrl: null,
    videoUrl: null,
    audioUrl: null,
    phase: 'pending',
    ...over,
  });

  it('无对白/旁白 → 空串', () => {
    expect(buildSrtContent([shot({})])).toBe('');
  });

  it('按镜头时长累积时间轴：对白优先、旁白兜底、无词镜头推进时间', () => {
    const srt = buildSrtContent([
      shot({ sceneNo: 1, dialogue: '林薇：你来了。', durationSec: 4 }),
      shot({ sceneNo: 2, narration: '', dialogue: '', durationSec: 6 }), // 无词，仅推进
      shot({ sceneNo: 3, narration: '深夜的酒店走廊。', durationSec: 5 }),
    ]);
    expect(srt).toContain('1\n00:00:00,000 --> 00:00:04,000\n林薇：你来了。');
    // 第二条从 10s 起（4s + 6s 无词镜头推进）
    expect(srt).toContain('2\n00:00:10,000 --> 00:00:15,000\n深夜的酒店走廊。');
  });
});
