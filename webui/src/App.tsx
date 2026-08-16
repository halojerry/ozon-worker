import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import { useAuth } from './stores/auth'
import Login from './pages/Login'
import Home from './pages/Home'
import CollectBox from './pages/CollectBox'
import Products from './pages/Products'
import Stores from './pages/Stores'
import Tasks from './pages/Tasks'
import ImageStudio from './pages/ImageStudio'
import OnSale from './pages/OnSale'
import Templates from './pages/Templates'
import Orders from './pages/Orders'
import Admin from './pages/Admin'
import PricingTool from './pages/PricingTool'
import Bestsellers from './pages/Bestsellers'
import DataScreen from './pages/DataScreen'

/** 受保护路由：未登录一律回 /login */
function Protected({ children }: { children: React.ReactNode }) {
  const authed = useAuth()
  if (!authed) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

/**
 * 路由表（T4 脚手架）：
 *   /login          登录页（token → /api/v1/auth/verify → localStorage）
 *   /               受保护布局壳（左侧导航 + Outlet）
 *   /collect-box    采集箱     T10
 *   /products       商品编辑   T10b
 *   /stores         店铺管理   T11
 *   /tasks          任务进度   T12
 *   /image-studio   生图工作台 T13
 */
export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route index element={<Home />} />
        <Route path="collect-box" element={<CollectBox />} />
        <Route path="products" element={<Products />} />
        <Route path="products/new" element={<Products />} />
        <Route path="products/:draftId" element={<Products />} />
        <Route path="stores" element={<Stores />} />
        <Route path="templates" element={<Templates />} />
        <Route path="tasks" element={<Tasks />} />
        <Route path="on-sale" element={<OnSale />} />
        <Route path="orders" element={<Orders />} />
        <Route path="pricing-tool" element={<PricingTool />} />
        <Route path="bestsellers" element={<Bestsellers />} />
        <Route path="data-screen" element={<DataScreen />} />
        <Route path="admin" element={<Admin />} />
        <Route path="image-studio" element={<ImageStudio />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
