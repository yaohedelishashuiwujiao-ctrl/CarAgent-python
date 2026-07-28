import { Tabs } from "antd";

import { DatasetPage } from "./DatasetPage";
import { EvidencePage } from "./EvidencePage";
import { VisionPage } from "./VisionPage";

export function AIDataFactoryPage() {
  return (
    <Tabs
      className="workspace-tabs"
      defaultActiveKey="evidence"
      items={[
        { key: "evidence", label: "证据中心", children: <EvidencePage /> },
        { key: "datasets", label: "数据集标注", children: <DatasetPage /> },
        { key: "vision", label: "图片识别", children: <VisionPage /> },
      ]}
    />
  );
}
