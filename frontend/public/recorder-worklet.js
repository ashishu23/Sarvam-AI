/* Local PCM capture only; no network or storage. */
class EagleRecorder extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) this.port.postMessage(channel.slice());
    return true;
  }
}
registerProcessor('eagle-recorder', EagleRecorder);
