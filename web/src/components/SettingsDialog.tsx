/** Model settings: default model, provider API keys, per-strategy
 *  overrides, and default generation params. Keys are write-only here —
 *  the server only ever returns a last-4 hint; typing a new value
 *  replaces, the × button deletes. */
import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { useForklore } from '@/lib/store'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

const COMMON_PROVIDERS = ['openai', 'anthropic', 'openrouter', 'google', 'mistral', 'deepseek']

interface KeyRow { provider: string; value: string; hint: string; cleared: boolean; custom: boolean }

export default function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const settings = useForklore((s) => s.settings)
  const strategies = useForklore((s) => s.strategies)
  const saveSettings = useForklore((s) => s.saveSettings)

  const [defaultModel, setDefaultModel] = useState('')
  const [rows, setRows] = useState<KeyRow[]>([])
  const [overrides, setOverrides] = useState<Record<string, string>>({})
  const [paramsJson, setParamsJson] = useState('')
  const [paramsError, setParamsError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // (re)initialise the form whenever the dialog opens
  useEffect(() => {
    if (!open || !settings) return
    setDefaultModel(settings.default_model ?? '')
    const providers = new Set([...COMMON_PROVIDERS, ...Object.keys(settings.api_keys)])
    setRows([...providers].map((p) => ({
      provider: p, value: '', hint: settings.api_keys[p] ?? '', cleared: false,
      custom: !COMMON_PROVIDERS.includes(p),
    })))
    setOverrides(Object.fromEntries(strategies.map((s) => [s, settings.strategy_models[s] ?? ''])))
    setParamsJson(Object.keys(settings.default_params).length
      ? JSON.stringify(settings.default_params, null, 2) : '')
    setParamsError(null)
  }, [open, settings, strategies])

  const save = async () => {
    let params: Record<string, unknown> = {}
    const raw = paramsJson.trim()
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
          throw new Error('must be a JSON object')
        }
        params = parsed
      } catch (e) {
        setParamsError(`invalid JSON: ${e instanceof Error ? e.message : e}`)
        return
      }
    } else if (settings && Object.keys(settings.default_params).length) {
      // blanked out: delete every existing param key
      params = Object.fromEntries(Object.keys(settings.default_params).map((k) => [k, '']))
    }

    const apiKeys: Record<string, string> = {}
    for (const r of rows) {
      if (r.cleared) apiKeys[r.provider] = ''
      else if (r.value.trim()) apiKeys[r.provider] = r.value.trim()
    }
    const strategyModels: Record<string, string> = {}
    for (const [sid, model] of Object.entries(overrides)) {
      const had = settings?.strategy_models[sid] ?? ''
      const m = model.trim()
      if (m && m !== had) strategyModels[sid] = m
      else if (!m && had) strategyModels[sid] = ''
    }

    setSaving(true)
    const ok = await saveSettings({
      default_model: defaultModel.trim() || null,
      api_keys: apiKeys,
      strategy_models: strategyModels,
      default_params: params,
    })
    setSaving(false)
    if (ok) onClose()
  }

  const setRow = (i: number, patch: Partial<KeyRow>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="font-serif italic text-xl">Model settings</DialogTitle>
          <DialogDescription>
            Stored on the server. Without a model configured, strategies run on the
            built-in demo model — everything keeps working offline.
          </DialogDescription>
        </DialogHeader>

        {settings && !settings.litellm_available && (
          <p className="text-xs rounded border border-amber-500/40 bg-amber-500/10 text-amber-200 px-3 py-2">
            litellm is not installed on this server — a configured model will not run
            (<code>pip install litellm</code> on the server host).
          </p>
        )}

        <div className="space-y-2">
          <Label htmlFor="default-model">Default model</Label>
          <Input
            id="default-model"
            value={defaultModel}
            onChange={(e) => setDefaultModel(e.target.value)}
            placeholder="gpt-4o, anthropic/claude-sonnet-4-5, ollama/llama3.1 …"
            className="font-mono text-xs"
          />
        </div>

        <div className="space-y-2">
          <Label>API keys</Label>
          <div className="space-y-1.5">
            {rows.map((r, i) => (
              <div key={r.provider} className="flex items-center gap-2">
                {r.custom ? (
                  <Input
                    value={r.provider}
                    onChange={(e) => setRow(i, { provider: e.target.value.trim() })}
                    className="w-32 font-mono text-xs"
                    placeholder="provider"
                  />
                ) : (
                  <span className="w-32 shrink-0 font-mono text-xs text-muted-foreground">
                    {r.provider}
                  </span>
                )}
                <Input
                  type="password"
                  value={r.value}
                  disabled={r.cleared}
                  onChange={(e) => setRow(i, { value: e.target.value, cleared: false })}
                  placeholder={r.hint ? `set: ${r.hint} — type to replace` : 'not set'}
                  className="font-mono text-xs"
                />
                {(r.hint || r.value) && (
                  <button
                    className="text-muted-foreground hover:text-destructive transition-colors"
                    title={r.cleared ? 'undo removal' : 'remove this key'}
                    onClick={() => setRow(i, r.cleared
                      ? { cleared: false } : { cleared: true, value: '' })}
                  >
                    <X className={`size-3.5 ${r.cleared ? 'text-destructive' : ''}`} />
                  </button>
                )}
              </div>
            ))}
          </div>
          <button
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setRows((rs) => [...rs, { provider: '', value: '', hint: '', cleared: false, custom: true }])}
          >
            <Plus className="size-3" /> add provider
          </button>
        </div>

        {strategies.length > 0 && (
          <div className="space-y-2">
            <Label>Per-strategy model override</Label>
            <div className="space-y-1.5">
              {strategies.map((sid) => (
                <div key={sid} className="flex items-center gap-2">
                  <span className="w-44 shrink-0 font-mono text-xs text-muted-foreground truncate"
                    title={sid}>
                    {sid}
                  </span>
                  <Input
                    value={overrides[sid] ?? ''}
                    onChange={(e) => setOverrides((o) => ({ ...o, [sid]: e.target.value }))}
                    placeholder="use default model"
                    className="font-mono text-xs"
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2">
          <Label htmlFor="default-params">Default generation params (JSON)</Label>
          <Textarea
            id="default-params"
            value={paramsJson}
            onChange={(e) => { setParamsJson(e.target.value); setParamsError(null) }}
            placeholder='{"temperature": 0.7, "max_tokens": 600}'
            rows={3}
            className="font-mono text-xs"
          />
          {paramsError && <p className="text-xs text-destructive">{paramsError}</p>}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" size="sm" onClick={onClose}>cancel</Button>
          <Button size="sm" disabled={saving} onClick={() => void save()}>
            {saving ? 'saving…' : 'save settings'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
