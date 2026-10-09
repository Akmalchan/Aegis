// Ambient stubs for the private Guild registry packages, used ONLY for offline `tsc` checks
// (tsconfig.check.json in each agent dir). After `guild auth login && npm install @guildai/agents-sdk@latest`
// the real typings take over and this file must NOT be in the include list.
// Shapes mirror @guildai/agents-sdk 0.7.8, @guildai-services/guildai~github 2.0.3, aegis-scanner 1.0.0.
declare module "@guildai/agents-sdk" {
  export type ToolMap = Record<string, unknown>
  export function pick<T extends ToolMap, K extends keyof T>(tools: T, names: readonly K[]): Pick<T, K>
  export interface LlmPreference { provider: "anthropic" | "openai" | "gemini" | "meta" | "deepseek" | "alibaba" | "moonshot" | "zai"; model?: string }
  export interface LlmAgentConfig {
    description?: string
    inputSchema?: unknown
    inputTemplate?: string
    tools: ToolMap
    systemPrompt: string
    mode?: "one-shot" | "multi-turn"
    llmPreferences?: LlmPreference[]
    useWorkspaceAgents?: boolean
    toolCallResponseStream?: "visible" | "hidden"
  }
  export function llmAgent(config: LlmAgentConfig): unknown
  export const guildTools: ToolMap
  export const skillsTools: ToolMap
}
declare module "@guildai-services/guildai~github" {
  export const gitHubTools: Record<string, unknown>
}
declare module "@guildai-services/__OWNER__~aegis-scanner" {
  export const AegisScannerTools: Record<string, unknown>
}
