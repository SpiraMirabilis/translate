import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)

// Tell the pre-render fallback watchdog (see index.html) that the SPA mounted,
// and remove the fallback banner if a slow load caused it to appear first.
window.__APP_RENDERED = true
const fb = document.getElementById('spa-fallback')
if (fb) fb.style.display = 'none'
