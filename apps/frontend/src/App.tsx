import {
  ApiOutlined,
  BookOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSyncOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Button, Layout, Menu, Typography } from "antd";
import { useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import { AIDataFactoryPage } from "./pages/AIDataFactoryPage";
import { AgentPage } from "./pages/AgentPage";
import { AssetWorkbenchPage } from "./pages/AssetWorkbenchPage";
import { DataOpsPage } from "./pages/DataOpsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DatasetPage } from "./pages/DatasetPage";
import { EvidencePage } from "./pages/EvidencePage";
import { KnowledgePage } from "./pages/KnowledgePage";
import { ImportExportPage } from "./pages/ImportExportPage";
import { MetadataPage } from "./pages/MetadataPage";
import { PermissionPage } from "./pages/PermissionPage";
import { StructureTreePage } from "./pages/StructureTreePage";
import { VehiclePage } from "./pages/VehiclePage";
import { VisualizationPage } from "./pages/VisualizationPage";
import { VisionPage } from "./pages/VisionPage";

const { Header, Sider, Content } = Layout;

const menuItems = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">数据概览</Link> },
  { key: "/assets", icon: <DatabaseOutlined />, label: <Link to="/assets">数据资产</Link> },
  { key: "/metadata", icon: <ApiOutlined />, label: <Link to="/metadata">动态元数据</Link> },
  { key: "/factory", icon: <ExperimentOutlined />, label: <Link to="/factory">AI 数据工厂</Link> },
  { key: "/knowledge", icon: <BookOutlined />, label: <Link to="/knowledge">知识工作台</Link> },
  { key: "/ops", icon: <FileSyncOutlined />, label: <Link to="/ops">数据运营</Link> },
  { key: "/agent", icon: <RobotOutlined />, label: <Link to="/agent">AI Agent</Link> },
  { key: "/permissions", icon: <SafetyCertificateOutlined />, label: <Link to="/permissions">权限管理</Link> },
];

function selectedMenuKey(pathname: string) {
  if (["/vehicles", "/tree"].includes(pathname)) return "/assets";
  if (["/datasets", "/vision", "/evidence"].includes(pathname)) return "/factory";
  if (["/knowledge"].includes(pathname)) return "/knowledge";
  if (["/import-export", "/visualization"].includes(pathname)) return "/ops";
  return pathname;
}

export default function App() {
  const location = useLocation();
  const [navCollapsed, setNavCollapsed] = useState(false);

  return (
    <Layout className="app-shell">
      <Sider width={240} collapsedWidth={72} collapsed={navCollapsed} theme="light" className="app-sider">
        <div className={navCollapsed ? "brand collapsed" : "brand"}>{navCollapsed ? "底盘" : "底盘竞品平台"}</div>
        <Menu mode="inline" selectedKeys={[selectedMenuKey(location.pathname)]} items={menuItems} />
      </Sider>
      <Layout>
        <Header className="app-header">
          <div className="app-header-main">
            <Button
              size="small"
              icon={navCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setNavCollapsed((collapsed) => !collapsed)}
            />
            <Typography.Title level={4}>汽车底盘竞品数据平台</Typography.Title>
          </div>
          <Typography.Text type="secondary">数据平台 / 自动采集 / AI Agent / 可视化 / AI 提案</Typography.Text>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/assets" element={<AssetWorkbenchPage />} />
            <Route path="/vehicles" element={<VehiclePage />} />
            <Route path="/metadata" element={<MetadataPage />} />
            <Route path="/factory" element={<AIDataFactoryPage />} />
            <Route path="/datasets" element={<DatasetPage />} />
            <Route path="/tree" element={<StructureTreePage />} />
            <Route path="/vision" element={<VisionPage />} />
            <Route path="/evidence" element={<EvidencePage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/ops" element={<DataOpsPage />} />
            <Route path="/import-export" element={<ImportExportPage />} />
            <Route path="/visualization" element={<VisualizationPage />} />
            <Route path="/agent" element={<AgentPage />} />
            <Route path="/permissions" element={<PermissionPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
