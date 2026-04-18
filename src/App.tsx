import { Outlet } from "react-router-dom";
import "./styles/global.css";
import { JSX } from "react";

function App(): JSX.Element {
  return <Outlet />;
}

export default App;
