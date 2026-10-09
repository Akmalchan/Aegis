// Offline type shims so `tsc -p tsconfig.offline.json --noEmit` works WITHOUT access to the Guild
// npm registry (https://app.guild.ai/npm/ answers 401 until `guild auth login`). They mirror the
// shapes documented in the Guild CLI's bundled docs (agent-dev.md, integrations.md) and are only
// included by tsconfig.offline.json. After `guild auth login && npm install`, use plain `tsc`.
//
// LIMITATION: tool NAMES are not validated by these shims (gitHubTools is Record<string, ...>).
// The real packages type `pick()` keys, so a typo surfaces only after a real npm install.
declare module "@guildai/agents-sdk" {
  import type { ZodType } from "zod"
  export interface ToolDefinition {
    description?: string
    inputSchema?: unknown
  }
  export type ToolSet = Record<string, ToolDefinition>
  export function pick<T extends ToolSet, K extends keyof T>(tools: T, keys: readonly K[]): Pick<T, K>
  export function omit<T extends ToolSet, K extends keyof T>(tools: T, keys: readonly K[]): Omit<T, K>
  export const skillsTools: { skills_search: ToolDefinition; skills_activate: ToolDefinition }
  export const consoleTools: { console_log: ToolDefinition }
  export const userInterfaceTools: ToolSet
  export const guildTools: ToolSet
  export interface LlmPreference {
    provider: string
    model?: string
  }
  export interface LlmAgentConfig {
    description: string
    tools: ToolSet
    systemPrompt: string
    inputSchema?: ZodType
    outputSchema?: ZodType
    inputTemplate?: string
    mode?: "one-shot" | "multi-turn"
    useWorkspaceAgents?: boolean
    llmPreferences?: LlmPreference[]
  }
  export function llmAgent(config: LlmAgentConfig): unknown
}

declare module "@guildai-services/guildai~github" {
  import type { ToolDefinition } from "@guildai/agents-sdk"
  export const gitHubTools: Record<string, ToolDefinition>
}

declare module "@guildai-services/__OWNER__~aegis-scanner" {
  import type { ToolDefinition } from "@guildai/agents-sdk"
  export const AegisScannerTools: {
    aegis_scanner_scan_diff: ToolDefinition
    aegis_scanner_scan_full: ToolDefinition
    aegis_scanner_record_action: ToolDefinition
    aegis_scanner_fleet_insights: ToolDefinition
  }
}
