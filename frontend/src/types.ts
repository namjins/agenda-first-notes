export type WorkspaceId = 'ward_council' | 'bishopric'
export interface Workspace { id: WorkspaceId; name: string }

export interface AgendaNode { id: string; text: string; kind: 'group' | 'topic'; children: AgendaNode[] }

export interface Meeting {
  id: string
  title: string
  meeting_date: string | null
  state: string
  created_at: string
  updated_at: string
  coverage: { segments: { index: number; start: number; end: number; status: string; error?: string }[]; complete: boolean } | null
  carry_forward: string[]
  last_error: string | null
  agenda: { id: string; revision_no: number; tree: AgendaNode[]; source: string; approved_at: string | null; text: string } | null
  agenda_approved: boolean
  transcript: { id: string; version_no: number; length: number; source: string } | null
  images: { id: string; filename: string; extracted_text: string | null; position: number }[]
}

export interface Evidence { start: number; end: number; quote: string; transcript_version_id: string }

export interface Note {
  id: string
  meeting_id: string
  agenda_node_id: string | null
  position: number
  text: string
  category: string
  uncertainty: 'low' | 'medium' | 'high'
  follow_up: boolean
  owner: string | null
  calling_event: string | null
  status: 'verified' | 'uncertain' | 'user_edited' | 'needs_review' | 'unsupported' | 'manual_unverified'
  origin: 'model' | 'user'
  evidence: Evidence[]
  stale: boolean
  deleted: boolean
  name_confirmed?: boolean // passes the name check only thanks to a name the user confirmed (stored when derived)
}

export interface Finding {
  id: string
  round_no: number
  note_id: string | null
  severity: 'info' | 'low' | 'high'
  category: string
  message: string
  action: string | null
  resolved: boolean
  source: string
}

export interface ReviewView {
  meeting: Meeting
  notes: Note[]
  findings: Finding[]
  coverage: Meeting['coverage']
  markdown: string
  undiscussed_follow_ups: { agenda_node_id: string; agenda_text: string; follow_up_id: string; description: string; status: string; selected: boolean }[]
}

export interface Job { id: string; meeting_id: string | null; kind: string; status: string; progress: number; message: string; cancel_requested: boolean }

export interface FollowUp {
  id: string
  meeting_id: string | null
  agenda_topic: string | null
  description: string
  owner: string | null
  due_date: string | null
  status: 'proposed' | 'open' | 'blocked' | 'deferred' | 'completed' | 'unverified'
  origin: string
  evidence: Evidence[]
  updated_at: string
  events: { id: string; prev_status: string | null; new_status: string | null; note: string | null; source: string; created_at: string }[]
}

export interface Decision { id: string; meeting_id: string; agenda_topic: string | null; text: string; status: string; origin: string; evidence: Evidence[]; created_at: string }
export interface CallingEvent { id: string; meeting_id: string | null; person: string | null; calling: string | null; event_type: string; note: string | null; origin: string; evidence: Evidence[]; status: string | null; created_at: string }
export interface ScheduledEvent { id: string; meeting_id: string | null; title: string; event_date: string | null; supersedes_id: string | null; origin: string; status: string | null; created_at: string }
export interface AuditEvent { id: string; entity_type: string; entity_id: string; meeting_id: string | null; action: string; prev: unknown; new: unknown; source: string; created_at: string }
export interface SearchHit { entity_type: string; entity_id: string; meeting_id: string; meeting_date: string; status: string; title: string; snippet: string }
export interface ReferenceDoc { id: string; filename: string; kind: string; sha256: string; length: number; use_as_glossary: boolean; created_at: string }

export interface Member { preferred_name: string; full_name: string; spouse_name: string; callings: { calling: string; sustained: string }[] }
export interface NameCandidate { member_id: string; full_name: string; name: string; preferred: string; callings: string[]; reason: string; score: number }
export interface NameSuggestion { heard: string; start: number; count: number; quotes: string[]; agenda_item: string | null; orgs: string[]; candidates: NameCandidate[] }
export interface NameAnswer { id: string; heard: string; name: string; preferred: string; status: 'confirmed' | 'rejected'; member_id: string | null; in_transcript: boolean }
export interface AgendaNameSuggestion { node_id: string; start: number; end: number; written: string; candidates: { member_id: string; name: string; callings: string[] }[] }
export interface NameSuggestions { member_count: number; suggestions: NameSuggestion[]; answers: NameAnswer[]; needs_regeneration: boolean }

export interface ModelInfo { name: string; size_bytes: number | null; parameter_size: string | null; family: string | null; capabilities: string[] }
export interface ModelsResponse { connected: boolean; host: string; models: ModelInfo[]; warnings: string[]; ocr_backends?: string[] }
export interface Settings {
  models: {
    ollama_host: string; primary_model: string | null; reviewer_model: string | null; vision_model: string | null
    context_length: number; temperature: number; max_output_tokens: number; max_review_rounds: number; segment_chars: number; segment_overlap_chars: number
  }
  theme: string
}
