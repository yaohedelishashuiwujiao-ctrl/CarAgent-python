import { Tabs } from "antd";

import { AutohomePage } from "./AutohomePage";
import { ImportExportPage } from "./ImportExportPage";
import { VisualizationPage } from "./VisualizationPage";

export function DataOpsPage() {
  return (
    <Tabs
      className="workspace-tabs"
      defaultActiveKey="import-export"
      items={[
        { key: "import-export", label: "导入导出", children: <ImportExportPage /> },
        { key: "autohome", label: "汽车之家数据源", children: <AutohomePage /> },
        { key: "visualization", label: "数据可视化", children: <VisualizationPage /> },
      ]}
    />
  );
}
