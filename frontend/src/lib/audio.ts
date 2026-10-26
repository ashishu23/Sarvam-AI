export function encodeWav(samples: Float32Array, sourceRate: number, targetRate = 16000): Blob {
  const ratio = sourceRate / targetRate;
  const length = Math.floor(samples.length / ratio);
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const word = (offset: number, value: string) => [...value].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  word(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true); word(8, 'WAVE'); word(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, targetRate, true); view.setUint32(28, targetRate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  word(36, 'data'); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    const from = Math.floor(i * ratio), to = Math.min(samples.length, Math.floor((i + 1) * ratio));
    let value = 0; for (let j = from; j < to; j++) value += samples[j];
    value = Math.max(-1, Math.min(1, value / Math.max(1, to - from)));
    view.setInt16(44 + i * 2, value * (value < 0 ? 32768 : 32767), true);
  }
  return new Blob([buffer], {type: 'audio/wav'});
}
