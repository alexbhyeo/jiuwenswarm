import './i18n';
import ReactDOM from 'react-dom/client';
import App from './App.tsx'
// v0.9's basicCatalog components ship their own CSS Modules, imported as a
// side effect of the package's JS (see @a2ui/react's "sideEffects": ["*.css"]
// in package.json) - unlike v0.8, there is no separate injectStyles() call.
import './features/a2ui/messageProcessor';
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
  <App />,
)
