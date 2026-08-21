import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'

// 预览模式：无登录态时自动注入管理员演示会话
if (!localStorage.getItem('ozon_webui_token')) {
  localStorage.setItem('ozon_webui_token', 'preview-demo-token')
  localStorage.setItem('ozon_webui_role', 'admin')
  localStorage.setItem('ozon_webui_username', 'Admin Demo')
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
