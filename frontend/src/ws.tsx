import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import type { WorkspaceId } from './types'

interface Ctx { ws: WorkspaceId; setWs: (w: WorkspaceId) => void; wsName: string }
const WsContext = createContext<Ctx>({ ws: 'ward_council', setWs: () => {}, wsName: 'Ward Council' })
export const WS_NAMES: Record<WorkspaceId, string> = { ward_council: 'Ward Council', bishopric: 'Bishopric' }

export function WsProvider({ children }: { children: ReactNode }) {
  const [ws, setWsState] = useState<WorkspaceId>(() => {
    try { return (localStorage.getItem('afn_ws') as WorkspaceId) || 'ward_council' } catch { return 'ward_council' }
  })
  const setWs = (w: WorkspaceId) => { setWsState(w); try { localStorage.setItem('afn_ws', w) } catch { /* private mode */ } }
  useEffect(() => { document.title = `${WS_NAMES[ws]} · Agenda First Notes` }, [ws])
  return <WsContext.Provider value={{ ws, setWs, wsName: WS_NAMES[ws] }}>{children}</WsContext.Provider>
}

export const useWs = () => useContext(WsContext)
