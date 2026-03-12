import Dashboard from "./components/Dashboard"
import { useWebSocket } from "./hooks/useWebSocket"

function App() {
  const { state, connected, sendCommand, pnlHistory } = useWebSocket()

  return (
    <Dashboard
      state={state}
      connected={connected}
      onCommand={sendCommand}
      pnlHistory={pnlHistory}
    />
  )
}

export default App
