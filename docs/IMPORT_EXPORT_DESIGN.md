# 导入导出设计

## 目标

导入导出要支持数据维护员批量维护整车、零部件和动态属性值，同时保证底层 ID 关联正确。

## 导入类型

第一阶段支持三类模板：

### 1. 整车实例导入模板

用于批量创建车型。

固定列：

```text
vehicle_code
vehicle_name
brand
model_name
year
```

动态列来自“整车”实体类型的属性定义。

### 2. 零部件实体类型导入模板

用于批量维护零部件字典。

固定列：

```text
entity_type_code
entity_type_name
default_system_code
description
```

示例：

```text
upper_control_arm,上控制臂/上摆臂,suspension,悬架上控制臂
brake_disc,制动盘,braking,制动系统摩擦件
```

### 3. 零部件实例导入模板

用于给某个整车批量导入零部件数据。

固定列：

```text
vehicle_code
system_code
entity_type_code
component_code
component_name
```

动态列来自对应零部件实体类型的属性定义。

示例：

```text
XPENG_X9,suspension,upper_control_arm,XPENG_X9_LF_UCA,小鹏X9左前上摆臂,左前,2.8,铝合金
```

## 模板下载

模板下载必须根据当前元数据实时生成：

```text
选择导入对象
  -> 选择实体类型
  -> 系统读取 entity_attribute
  -> 生成 CSV/Excel 表头
  -> 下载模板
```

模板表头分两段：

- 固定系统列
- 动态属性列

动态属性列建议格式：

```text
属性名称[属性编码|类型|单位]
```

例如：

```text
重量[weight|number|kg]
材料[material|text]
```

这样用户可读，系统也能稳定解析。

## 导入流程

```text
上传文件
  -> 选择模板类型
  -> 解析表头
  -> 匹配系统列和动态属性列
  -> 校验数据
  -> 预览有效行和错误行
  -> 确认入库
  -> 写入 import_job / import_job_item
```

## 导出类型

第一阶段支持：

- 当前车型结构树导出
- 当前查询结果导出
- 某实体类型属性模板导出
- 错误行导出

## 导出字段

导出同样分固定列和动态列：

```text
vehicle_code
vehicle_name
system_code
system_name
entity_type_code
entity_type_name
component_code
component_name
动态属性...
```

## 权限

导入导出需要独立权限：

- `asset:import`
- `asset:export`
- `metadata:import`
- `metadata:export`

导出必须受数据范围约束，例如分析师只能导出自己有权限查看的数据。
