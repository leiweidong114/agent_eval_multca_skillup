import { parentPort as nodeParentPort, workerData } from "node:worker_threads";

function wrapPort(port) {
  const listeners = new Map();
  return {
    addEventListener(type, listener) {
      if (type !== "message") return;
      const wrapped = (data) => listener({ data, ports: [] });
      listeners.set(listener, wrapped);
      port.on("message", wrapped);
    },
    removeEventListener(type, listener) {
      if (type !== "message") return;
      const wrapped = listeners.get(listener);
      if (wrapped) port.off("message", wrapped);
      listeners.delete(listener);
    },
    on(type, listener) {
      if (type === "message") {
        const wrapped = (data) => listener({ data, ports: [] });
        listeners.set(listener, wrapped);
        port.on(type, wrapped);
      } else {
        port.on(type, listener);
      }
      return this;
    },
    off(type, listener) {
      const wrapped = listeners.get(listener) ?? listener;
      port.off(type, wrapped);
      listeners.delete(listener);
      return this;
    },
    once(type, listener) {
      port.once(type, listener);
      return this;
    },
    postMessage(value, transferList = []) {
      port.postMessage(value, transferList);
    },
    start() {
      port.start?.();
    },
    close() {
      port.close();
    },
  };
}

const parentListeners = new Map();
const electronParentPort = {
  on(type, listener) {
    if (type !== "message") return this;
    const wrapped = (envelope) => {
      const ports = (envelope?.__electronPorts ?? []).map(wrapPort);
      listener({ data: envelope?.__electronData ?? envelope, ports });
    };
    parentListeners.set(listener, wrapped);
    nodeParentPort.on("message", wrapped);
    return this;
  },
  off(type, listener) {
    if (type !== "message") return this;
    const wrapped = parentListeners.get(listener);
    if (wrapped) nodeParentPort.off("message", wrapped);
    parentListeners.delete(listener);
    return this;
  },
  postMessage(data, transferList = []) {
    nodeParentPort.postMessage(
      { __electronData: data, __electronPorts: transferList },
      transferList,
    );
  },
};

process.env.ZCODE_DATA_BASE_DIR = workerData.zcodeDataBaseDir;
Object.defineProperty(process, "parentPort", {
  configurable: true,
  value: electronParentPort,
});
Object.defineProperty(process, "resourcesPath", {
  configurable: true,
  value: workerData.resourcesPath,
});
Object.defineProperty(process.versions, "electron", {
  configurable: true,
  value: workerData.electronVersion ?? "41.0.3",
});

await import(workerData.hostIndexUrl);
