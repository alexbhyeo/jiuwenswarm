import './i18n';
import ReactDOM from 'react-dom/client';
import App from './App.tsx'
// @a2ui/react v0.9's basicCatalog CSS (v0_9/index.css) is never actually
// imported by the package's own JS and isn't in its exports map either, so
// it can never end up in our bundle no matter what - see a2ui.css for a
// verbatim copy of its rules, which this import brings in.
import './features/a2ui/messageProcessor';
import { MarkdownContext } from '@a2ui/react/v0_9';
import { renderMarkdown } from '@a2ui/markdown-it';
import './index.css'
import './features/a2ui/a2ui.css'

function flagA2UIIconFontAvailability() {
  if (typeof document === 'undefined' || !('fonts' in document)) {
    return
  }

  const fonts = document.fonts
  const hasMaterialSymbols =
    fonts.check('20px "Material Symbols Outlined"') ||
    fonts.check('20px "Google Symbols"')

  document.documentElement.classList.toggle(
    'a2ui-material-symbols-unavailable',
    !hasMaterialSymbols
  )
}

flagA2UIIconFontAvailability()
void document.fonts?.ready.then(flagA2UIIconFontAvailability)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <MarkdownContext.Provider value={renderMarkdown}>
    <App />
  </MarkdownContext.Provider>,
)
