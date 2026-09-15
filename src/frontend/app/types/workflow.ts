export interface ProviderConfig { id: string; name: string; baseUrl: string; credentialRef: string; enabled: boolean }
export type ModelType = 'text' | 'multimodal' | 'embedding'
export interface ModelConfig { modelType: ModelType; id: string; name: string; providerId: string; modelName: string; parameters: Record<string, unknown>; contextBudgetTokens: number; inputTypes: string[]; enabled: boolean }
export interface AgentConfig { description: string; callable: boolean; id: string; name: string; instructions: string; modelConfigId: string; allowedTools: string[]; enabled: boolean }
export interface WorkflowConfig { id: string; name: string; kind: 'single' | 'frontend-review'; maxRevisions: number; enabled: boolean; steps: { step_key: string; agent_id: string | null }[] }
export interface AgentExecution { parentTaskId?: string | null; id: string; agentId: string; agentName?: string; modelName?: string; stepKey: string; iteration: number; status: string; output: string; error: string }
export interface Artifact { id: string; kind: string; mediaType: string; metadata: { url?: string; viewport?: { width: number; height: number } }; base64?: string }
export interface ReviewFinding { id: string; severity: string; description: string; artifactId: string; location: string; suggestion: string }
export interface VisualReview { id: string; iteration: number; verdict: string; summary: string; artifactIds: string[]; findings: ReviewFinding[] }
export interface RunDetail { id: string; agentRuns: AgentExecution[]; artifacts: Artifact[]; reviews: VisualReview[] }

export interface AgentStatusExecution { id: string; run_id: string; session_id: string; status: string; task: string; output: string; error: string; updated_at: string }

export interface AgentStatus {
  id: string
  modelType: ModelType
  modelName: string
  issues: string[]
  runningCount: number
  waitingCount: number
  test: { status?: 'passed' | 'failed' | 'stale'; testedAt?: string; error?: string }
  active: AgentStatusExecution[]
  latest: AgentStatusExecution | null
}
