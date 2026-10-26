import { useEffect, useRef, useState } from 'react';
import { Mic, Square } from 'lucide-react';
import { encodeWav } from '../lib/audio';
import { tr } from '../lib/i18n';
import type { Language } from '../lib/types';

export function Recorder({language, disabled, onRecording, onError, onStart}: {language: Language; disabled?: boolean; onRecording: (blob: Blob) => void; onError: (message: string) => void; onStart?: () => void}) {
  const [recording, setRecording] = useState(false), [seconds, setSeconds] = useState(0), [starting, setStarting] = useState(false);
  const resources = useRef<{stream: MediaStream; context: AudioContext; node: AudioWorkletNode; source: MediaStreamAudioSourceNode; timer: number; timeout: number; chunks: Float32Array[]} | null>(null);
  const cleanup = (submit: boolean) => {
    const data = resources.current; if (!data) return;
    clearInterval(data.timer); clearTimeout(data.timeout); data.node.disconnect(); data.source.disconnect(); data.stream.getTracks().forEach(t => t.stop());
    if (submit) {
      const size = data.chunks.reduce((n, c) => n + c.length, 0), samples = new Float32Array(size);
      let offset = 0; data.chunks.forEach(c => {samples.set(c, offset); offset += c.length;});
      onRecording(encodeWav(samples.subarray(0, data.context.sampleRate * 25), data.context.sampleRate));
    }
    void data.context.close(); resources.current = null; setRecording(false); setSeconds(0);
  };
  useEffect(() => () => {
    const data = resources.current;
    if (data) { clearInterval(data.timer); clearTimeout(data.timeout); data.stream.getTracks().forEach(t => t.stop()); data.node.disconnect(); void data.context.close(); }
  }, []);
  const start = async () => {
    setStarting(true); onStart?.(); let stream: MediaStream | undefined; let context: AudioContext | undefined;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone needs HTTPS or localhost. You can continue by typing.');
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
      context = new AudioContext(); await context.audioWorklet.addModule('/recorder-worklet.js'); await context.resume();
      const source = context.createMediaStreamSource(stream), node = new AudioWorkletNode(context, 'eagle-recorder');
      const chunks: Float32Array[] = []; node.port.onmessage = event => chunks.push(event.data);
      const mute = context.createGain(); mute.gain.value = 0; source.connect(node); node.connect(mute); mute.connect(context.destination);
      resources.current = {stream, context, source, node, chunks, timer: window.setInterval(() => setSeconds(s => s + 1), 1000), timeout: window.setTimeout(() => cleanup(true), 24900)};
      setRecording(true);
    } catch (error) {stream?.getTracks().forEach(t => t.stop()); void context?.close(); onError(error instanceof Error ? `${error.message} You can continue by typing.` : 'Microphone unavailable. Type your question instead.');}
    finally {setStarting(false);}
  };
  return <button type="button" className={`icon-button mic ${recording ? 'recording' : ''}`} disabled={disabled || starting} aria-label={recording ? `${tr(language, 'stop')} ${seconds}s` : tr(language, 'record')} onClick={() => recording ? cleanup(true) : void start()}>{recording ? <><Square size={17}/><span>{seconds}s</span></> : <Mic size={19}/>}</button>;
}
