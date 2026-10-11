import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import "./officer.css";
import "./tokens.css";   // tokens and theme come last, so they win over the older sheets
import "./theme.css";
import App from "./App";
import { AuthProvider } from "./auth";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
);
