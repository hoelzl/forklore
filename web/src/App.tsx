import { useEffect } from 'react'
import TopBar from '@/components/TopBar'
import TreePane from '@/components/TreePane'
import ChatPane from '@/components/ChatPane'
import ComparePane from '@/components/ComparePane'
import PlayPane from '@/components/PlayPane'
import { useForklore } from '@/lib/store'

export default function App() {
  const init = useForklore((s) => s.init)
  const error = useForklore((s) => s.error)
  const clearError = useForklore((s) => s.clearError)
  const comparing = useForklore((s) => s.compare !== null)
  const play = useForklore((s) => s.play)

  useEffect(() => {
    void init()
  }, [init])

  return (
    <div className="h-screen w-screen flex flex-col overflow-hidden bg-background text-foreground">
      <TopBar />
      <div className="flex-1 grid min-h-0 grid-cols-1 md:grid-cols-[minmax(360px,42%)_1fr]">
        <div className="hidden md:flex min-h-0 flex-col">
          <TreePane />
        </div>
        {comparing ? <ComparePane /> : play ? <PlayPane /> : <ChatPane />}
      </div>
      {error && (
        <div
          className="fixed bottom-4 right-4 max-w-md border border-primary/60 bg-background px-4 py-3 text-sm cursor-pointer"
          onClick={clearError}
          title="click to dismiss"
        >
          <span className="eyebrow text-primary mr-2">error</span>
          {error}
        </div>
      )}
    </div>
  )
}
