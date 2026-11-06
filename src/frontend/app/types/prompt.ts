export interface PromptTemplate {
  id: string
  name: string
  description: string
  content: string
  updatedAt: string
}

export type PromptInput = Pick<PromptTemplate, 'name' | 'description' | 'content'>

export interface PromptCollection {
  version: 1
  items: PromptTemplate[]
}
