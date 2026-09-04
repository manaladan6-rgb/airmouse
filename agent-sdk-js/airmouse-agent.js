/**
 * airmouse-agent — AirMouse Agent SDK for JavaScript/TypeScript
 * (v14.5 §10, companion to airmouse-agent-core).
 *
 * Dependency-free.  Speaks AIP (AirMouse Interaction Protocol) JSON
 * messages over a pluggable local transport.  Never executes anything
 * by itself — every action crosses the core's permission gates.
 *
 * Primitives (§10): connect() capabilities() observe() targets()
 * execute() verify() task() stop() status()
 *
 * Usage (in-process handler — tests/embedded):
 *
 *   const { AirMouse, InProcessTransport } = require("./airmouse-agent");
 *   const air = new AirMouse(new InProcessTransport(handlerFn));
 *   await air.connect();
 *   await air.execute({ intent: "open my research project", verify: true });
 *
 * Usage (Node child process speaking AIP JSON-lines on stdio):
 *
 *   const air = new AirMouse(new StdioTransport("airmouse --aip-stdio"));
 *
 * TypeScript: this file ships .d.ts-style JSDoc types; the surface is
 * identical to the Python SDK.
 *
 * Copyright (c) AirMouse.  MIT License.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.AirMouseAgent = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var AIP_VERSION = "1.0";
  var MAX_MESSAGE_BYTES = 256 * 1024;

  /** @constructor */
  function AipError(code, message) {
    this.code = code;
    this.message = message;
    this.name = "AipError";
  }
  AipError.prototype = Object.create(Error.prototype);

  /** In-process transport: handler(wireJson) -> wireJson|object. */
  function InProcessTransport(handler) {
    this.handler = handler;
  }
  InProcessTransport.prototype.send = function (wire) {
    return this.handler(wire);
  };

  /** Node stdio transport (lazy require, §11). */
  function StdioTransport(command) {
    this.command = command;
    this.child = null;
  }
  StdioTransport.prototype.send = function (wire) {
    var cp = require("child_process");
    if (!this.child) {
      this.child = cp.spawn(this.command.split(" ")[0],
                            this.command.split(" ").slice(1),
                            { stdio: ["pipe", "pipe", "ignore"] });
    }
    this.child.stdin.write(wire + "\n");
    // synchronous readers are not portable in Node; one-shot exec per
    // call keeps this dependency-free and deterministic:
    var res = cp.spawnSync(this.command.split(" ")[0],
                           this.command.split(" ").slice(1),
                           { input: wire + "\n", encoding: "utf-8" });
    return res.stdout;
  };

  /**
   * @constructor
   * @param {Transport} transport object with send(wire)->wire
   * @param {{agentId?: string}=} opts
   */
  function AirMouse(transport, opts) {
    opts = opts || {};
    if (!transport || typeof transport.send !== "function") {
      throw new AipError("bad_message", "transport with send() required");
    }
    this.transport = transport;
    this.agentId = String(opts.agentId || "js-agent").slice(0, 64);
    this._counter = 0;
    this._connected = false;
    this._protocolVersion = null;
  }

  AirMouse.prototype._call = function (type, payload) {
    this._counter += 1;
    var message = {
      aip_version: AIP_VERSION,
      type: String(type).slice(0, 24),
      id: "msg-" + String(this._counter).padStart(8, "0"),
      agent_id: this.agentId,
      request_id: "",
      ts: Date.now() / 1000,
      payload: payload || {}
    };
    var wire = JSON.stringify(message);
    if (wire.length > MAX_MESSAGE_BYTES) {
      throw new AipError("bad_message", "outbound message too large");
    }
    var raw = this.transport.send(wire);
    var reply;
    try {
      reply = typeof raw === "string" ? JSON.parse(raw) : raw;
    } catch (e) {
      throw new AipError("bad_message", "unparseable reply");
    }
    if (!reply || typeof reply !== "object") {
      throw new AipError("timeout", "no reply");
    }
    if (reply.type === "error") {
      var p = reply.payload || {};
      throw new AipError(String(p.code || "failed"),
                         String(p.message || "").slice(0, 200));
    }
    var out = reply.payload || {};
    if (out.ok === undefined) { out.ok = true; }
    out._type = reply.type;
    return out;
  };

  /** @returns {Promise<boolean>} — sync transport resolves inline. */
  AirMouse.prototype.connect = function () {
    var st = this._call("status", {});
    this._connected = true;
    this._protocolVersion = st.protocol_version || null;
    return Promise.resolve(true);
  };

  AirMouse.prototype.capabilities = function () {
    return Promise.resolve(this._call("discover", {}));
  };

  AirMouse.prototype.observe = function () {
    return Promise.resolve(this._call("observe", {}));
  };

  AirMouse.prototype.targets = function (opts) {
    opts = opts || {};
    return Promise.resolve(this._call("target", {
      target: {
        kind: opts.kind || "semantic",
        value: opts.value || "",
        description: opts.description || "",
        coordinate_fallback: !!opts.coordinateFallback
      }
    }));
  };

  /**
   * @param {{intent?: string, action?: string, target?: Object,
   *          verify?: boolean}} spec
   */
  AirMouse.prototype.execute = function (spec) {
    spec = spec || {};
    var params = {};
    if (spec.intent) { params.intent = String(spec.intent).slice(0, 200); }
    if (spec.params) {
      Object.keys(spec.params).forEach(function (k) {
        params[k] = String(spec.params[k]).slice(0, 60);
      });
    }
    var payload = {
      action: String(spec.action || "click").slice(0, 40),
      verify: spec.verify !== false,
      params: params
    };
    if (spec.target) {
      payload.target = {
        kind: String(spec.target.kind || "semantic").slice(0, 20),
        value: String(spec.target.value || "").slice(0, 160),
        coordinate_fallback: !!spec.target.coordinate_fallback
      };
    }
    return Promise.resolve(this._call("execute", payload));
  };

  AirMouse.prototype.verify = function (actionId) {
    return Promise.resolve(this._call("verify",
                                      { action_id: String(actionId) }));
  };

  AirMouse.prototype.task = function (objective, steps) {
    return Promise.resolve(this._call("task", {
      objective: String(objective).slice(0, 200),
      steps: Array.isArray(steps) ? steps.slice(0, 64) : []
    }));
  };

  AirMouse.prototype.stop = function () {
    var out = this._call("stop", {});
    this._connected = false;
    return Promise.resolve(out);
  };

  AirMouse.prototype.status = function () {
    return Promise.resolve(this._call("status", {}));
  };

  return {
    AIP_VERSION: AIP_VERSION,
    AirMouse: AirMouse,
    AipError: AipError,
    InProcessTransport: InProcessTransport,
    StdioTransport: StdioTransport
  };
});
