import { Tabs } from "antd";

import { StructureTreePage } from "./StructureTreePage";
import { VehiclePage } from "./VehiclePage";

export function AssetWorkbenchPage() {
  return (
    <Tabs
      className="workspace-tabs"
      defaultActiveKey="vehicles"
      items={[
        { key: "vehicles", label: "车型与零部件实例", children: <VehiclePage /> },
        { key: "tree", label: "结构树查询", children: <StructureTreePage /> },
      ]}
    />
  );
}
