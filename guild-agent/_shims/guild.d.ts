// Ambient stubs for the private Guild registry packages, used ONLY for offline `tsc` checks
// (tsconfig.check.json in each agent dir). After `guild auth login && npm install` the real
// typings take over and this file must NOT be in the include list.
declare module "@guildai/agents-sdk" {
  export type ToolMap = Record<string, unknown>
  export function pick<T extends ToolMap, K extends keyof T>(tools: T, names: readonly K[]): Pick<T, K>
  export interface LlmPreference { provider: string; model?: string }
  export interface LlmAgentConfig {
    inputSchema?: unknown
    outputSchema?: unknown
    tools?: ToolMap
    mode?: "one-shot" | "multi-turn"
    systemPrompt: string
    inputTemplate?: string
    llmPreferences?: LlmPreference[]
    toolCallResponseStream?: "visible" | "hidden"
  }
  export function llmAgent(config: LlmAgentConfig): unknown
  export const guildTools: ToolMap
  export const skillsTools: ToolMap
}
declare module "@guildai-services/guildai~github" {
  export const gitHubTools: Record<string, unknown>
}
declare module "@guildai-services/guildai~email" {
  export const emailTools: Record<string, unknown>
}
declare module "@guildai-services/__OWNER__~aegis-scanner" {
  export const aegisScannerTools: Record<string, unknown>
}
