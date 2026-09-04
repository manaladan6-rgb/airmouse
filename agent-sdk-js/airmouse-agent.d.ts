/**
 * TypeScript surface for airmouse-agent (v14.5 §10).
 */
export declare const AIP_VERSION: string;

export declare class AipError extends Error {
  code: string;
  message: string;
  constructor(code: string, message: string);
}

export interface Transport {
  send(wire: string): string | object;
}

export declare class InProcessTransport implements Transport {
  constructor(handler: (wire: string) => string | object);
  send(wire: string): string | object;
}

export declare class StdioTransport implements Transport {
  constructor(command: string);
  send(wire: string): string | object;
}

export interface ExecuteSpec {
  intent?: string;
  action?: string;
  target?: { kind?: string; value?: string; coordinate_fallback?: boolean };
  params?: Record<string, string>;
  verify?: boolean;
}

export declare class AirMouse {
  constructor(transport: Transport, opts?: { agentId?: string });
  connect(): Promise<boolean>;
  capabilities(): Promise<object>;
  observe(): Promise<object>;
  targets(opts?: {
    kind?: string; value?: string; description?: string;
    coordinateFallback?: boolean;
  }): Promise<object>;
  execute(spec: ExecuteSpec): Promise<object>;
  verify(actionId: string): Promise<object>;
  task(objective: string, steps?: object[]): Promise<object>;
  stop(): Promise<object>;
  status(): Promise<object>;
}
