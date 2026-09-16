import { Component } from 'react'

/**
 * Confines a render error to one widget.
 *
 * Without this, a throw anywhere in the tree hits react-router's default
 * "Unexpected Application Error!" screen and the whole admin shell is gone.
 * That matters most for widgets mounted in Layout (PromptHost) or on several
 * pages (JobList): a bug in the job UI should not cost you the Books page.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.label || 'ErrorBoundary'}]`, error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    if (this.props.silent) return null
    return (
      <div className="text-xs text-rose-400 border border-rose-900 bg-rose-950/40 rounded px-3 py-2">
        {this.props.label || 'This panel'} failed to render.
        {' '}
        <button
          className="underline hover:text-rose-300"
          onClick={() => this.setState({ error: null })}
        >
          Retry
        </button>
      </div>
    )
  }
}
