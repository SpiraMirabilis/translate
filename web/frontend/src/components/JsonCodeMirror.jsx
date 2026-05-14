/**
 * JsonCodeMirror
 *
 * Thin wrapper around @uiw/react-codemirror preconfigured for JSON editing.
 * Lives in its own module so it (and the CodeMirror bundle it pulls in) can
 * be lazy-loaded — keeps JSON-editor JS out of routes that don't need it.
 */
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'

export default function JsonCodeMirror({ value, onChange, minHeight = '200px', maxHeight = '400px' }) {
  return (
    <CodeMirror
      value={value}
      onChange={onChange}
      extensions={[json()]}
      theme="dark"
      minHeight={minHeight}
      maxHeight={maxHeight}
      basicSetup={{
        lineNumbers: true,
        highlightActiveLine: true,
        foldGutter: true,
        bracketMatching: true,
        closeBrackets: true,
        autocompletion: false,
      }}
      style={{ fontSize: 13 }}
    />
  )
}
