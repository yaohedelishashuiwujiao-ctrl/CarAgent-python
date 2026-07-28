# 汽车底盘零部件本体与视觉类别

## 设计原则

平台数据和视觉模型要分层：

```text
实体类型：整车、控制臂、副车架、制动盘、转向节、半轴
实例：小鹏 X9 左前上摆臂、小鹏 X9 右前上摆臂
视觉类别：upper_control_arm、front_subframe、brake_disc、steering_knuckle、drive_shaft
位置属性：front/rear、left/right、upper/lower
```

原因：

- 视觉模型通常识别“零部件类型”，不稳定识别“左前/右前”这种依赖车辆朝向的业务位置。
- 平台数据库负责把检测结果挂接到车型、系统、位置和实例。
- 标注体系应避免把同一种几何零件拆成过多位置类，否则数据量会被稀释。

## 四大系统

### 悬架系统

作用：连接车身/副车架与车轮，约束车轮运动，支撑车身并吸收路面冲击。

核心零部件：

| 视觉类别 | 中文名 | 定义 | 视觉识别线索 | 平台属性建议 |
|---|---|---|---|---|
| `upper_control_arm` | 上控制臂/上摆臂 | 悬架连杆，一端连接车身/副车架，一端连接转向节或轮毂侧，用来约束车轮运动。 | 常见 A 字形、叉臂形或弯曲臂，两端有衬套/球头。 | 位置、材料、重量、成型工艺、衬套类型 |
| `lower_control_arm` | 下控制臂/下摆臂 | 车辆悬架中的下部控制连杆，承担更明显的轮端定位和载荷传递。 | 通常比上控制臂更大，靠近车轮下方，常连接弹簧/减振器。 | 材料、重量、连接点数量、是否铝合金 |
| `front_subframe` | 前副车架 | 独立于车身主结构的承载构件，用于安装悬架、转向机、动力总成等。 | 大型横向/框形金属结构，多个安装孔和衬套座。 | 材料、结构形式、重量、连接点 |
| `shock_absorber` | 减振器 | 将悬架振动能量转化为热能并耗散，用于抑制弹簧振荡。 | 筒状件，常带活塞杆，位于车轮附近上下连接。 | 类型、阻尼形式、供应商、是否 CDC |
| `strut` | 支柱总成 | 结构化悬架件，常把减振器、弹簧和转向支承功能组合在一起。 | 长筒状总成，外部常见弹簧包围。 | 是否麦弗逊、弹簧类型、上支座 |
| `anti_roll_bar` | 稳定杆/防倾杆 | 连接左右悬架，增加抗侧倾刚度，降低转弯侧倾。 | 横向 U 形/杆状件，通过小连杆连接悬架。 | 直径、材料、连接方式 |
| `stabilizer_link` | 稳定杆连杆 | 连接稳定杆与悬架件的小连杆。 | 细长杆，两端球头或衬套。 | 长度、球头类型、材料 |

### 制动系统

作用：通过摩擦将车辆动能转化为热能，实现减速和驻停。

核心零部件：

| 视觉类别 | 中文名 | 定义 | 视觉识别线索 | 平台属性建议 |
|---|---|---|---|---|
| `brake_disc` | 制动盘/刹车盘/rotor | 随车轮旋转的圆盘，制动片夹紧其两侧产生摩擦。 | 圆形金属盘，可能有通风槽、打孔、划线。 | 直径、厚度、通风形式、材料 |
| `brake_caliper` | 制动卡钳 | 容纳活塞和制动片，夹紧制动盘形成制动力。 | 包裹在制动盘边缘的钳形件，常有活塞包络。 | 固定/浮动、活塞数量、材料 |
| `brake_pad` | 制动片 | 与制动盘摩擦的耗材件，含背板和摩擦材料。 | 通常较小且藏在卡钳内，单独图片更容易识别。 | 摩擦材料、厚度、背板结构 |

### 转向系统

作用：把方向盘/转向机的运动传递到车轮，控制车辆行驶方向。

核心零部件：

| 视觉类别 | 中文名 | 定义 | 视觉识别线索 | 平台属性建议 |
|---|---|---|---|---|
| `steering_knuckle` | 转向节/羊角/upright | 轮毂、悬架臂、转向拉杆等连接的轮端承载件。 | 复杂铸/锻件，中心轮毂孔，多连接耳。 | 材料、工艺、重量、连接接口 |
| `tie_rod` | 转向拉杆 | 连接转向机或转向连杆到转向节，推拉车轮转向。 | 细长杆，两端常有球头，接近轮端前后侧。 | 长度、内外球头、材料 |
| `steering_rack` | 转向齿条/转向机 | 齿轮齿条机构把旋转输入转换为左右直线运动。 | 横向长壳体，两端连拉杆，带输入轴。 | EPS/液压、布置位置、传动比 |

### 动力/传动系统

作用：把动力总成输出传递到车轮。

核心零部件：

| 视觉类别 | 中文名 | 定义 | 视觉识别线索 | 平台属性建议 |
|---|---|---|---|---|
| `drive_shaft` | 半轴/驱动轴 | 把动力传递到驱动轮的轴类零件，常带 CV joint。 | 细长轴，两端有球笼/CV 防尘套。 | 长度、直径、材料、扭矩等级 |
| `cv_joint` | 等速万向节/CV joint | 允许驱动轴在夹角变化时近似等速传递扭矩。 | 球笼或杯状接头，常由橡胶防尘套覆盖。 | 内/外球笼、最大摆角、润滑形式 |
| `motor_mount` | 电机/动力总成悬置 | 连接动力总成与车身/副车架，并隔振。 | 橡胶衬套或液压悬置结构，常在动力总成周边。 | 刚度、材料、布置位置 |

## 初版视觉类别建议

第一版检测模型建议先训练 8 类，而不是直接上几十类：

```text
upper_control_arm
lower_control_arm
front_subframe
brake_disc
brake_caliper
steering_knuckle
tie_rod
drive_shaft
```

理由：

- 这些类别有明确几何形态，网络图片更容易收集。
- 与竞品分析直接相关：材料、重量、工艺、布置、轻量化。
- 既覆盖悬架/制动/转向/动力四大系统，又不会让初期标注压力失控。

## 标注规范

- 框住可见的完整零部件主体，不包含明显无关背景。
- 遮挡严重时只框可见主体，标注 `occluded=true`。
- 同一图片中多个同类零件分别标注。
- 不能确定类别时标为 `unknown_chassis_part`，不要强行归类。
- 左/右/前/后/上/下优先作为属性，不作为视觉类别。

## 参考资料

- Control arm: https://en.wikipedia.org/wiki/Control_arm
- Subframe: https://en.wikipedia.org/wiki/Subframe
- Shock absorber: https://en.wikipedia.org/wiki/Shock_absorber
- Anti-roll bar: https://en.wikipedia.org/wiki/Anti-roll_bar
- Disc brake: https://en.wikipedia.org/wiki/Disc_brake
- Brake pad: https://en.wikipedia.org/wiki/Brake_pad
- Steering knuckle: https://en.wikipedia.org/wiki/Steering_knuckle
- Steering linkage: https://en.wikipedia.org/wiki/Steering_linkage
- Tie rod: https://en.wikipedia.org/wiki/Tie_rod
- Constant-velocity joint: https://en.wikipedia.org/wiki/Constant-velocity_joint
