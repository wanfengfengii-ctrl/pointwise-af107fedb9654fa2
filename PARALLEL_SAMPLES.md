# 同源平行双瓶

现有取样台按巡检表逐瓶处理，设备只保存最近一次完成号和原码。平行样仍逐瓶采集，质控员凭组号领取同源两瓶及同一次质控裁决。原批次、定标、录制操作和单瓶接口见 `README.md`。

## 登记

`Workbench.submit_pair(batch_id, pair_id, members, threshold)` 在同一个已登记批次的队尾按给定顺序一次登记两个相邻瓶单，返回该顺序的两个 `BottleJob` 组成的元组。`members` 是恰有两项的列表或元组，每项沿用 `(order_id, bottle_id)`。两项工单号互异且全局未使用，两项瓶号互异。组号采用原标识的非空白文本规则，在组中全局唯一。`threshold` 以该批固定定标的单位计，接受非负整数、有理数字符串或 `Fraction`，布尔值和浮点数非法。

相同组号、批次、有序成员和精确阈值的重投返回原工单和原队位，已签的组结果照原号领取。阈值按数值判同，例如 `9/4` 与 `2.25`。异参、未知批次、工单占用和非法输入抛 `ValueError`，整次登记无效。普通 `submit_orders` 同参重投组成员可返回原工单，`sample` 为 `None`，其原组归属继续有效。

## 采集与判定

首瓶原样持久保全后才可释放设备，重开可继续采第二瓶，首瓶待判时不能领取任何正式结果。两瓶均沿用原设备终局和留样核对规则，按该批固定定标校验原码并换算。每次 `advance` 至多推进一瓶。`resume` 续办本批的在办瓶单，没有在办时报告最近确认的队位结果。`on_notices` 沿用 `resume` 的判定。

首瓶已保全并确认队位时返回 `StepResult`，其 `status` 为 `pair_staged`，`order_id` 为首瓶工单。完成整组且确认末瓶队位时，`status` 为 `pair_published`，`order_id` 为末瓶工单，新增 `pair` 属性携带组成果。两种结果的 `sample` 均为 `None`，`pair_staged` 的 `pair` 为 `None`。后续重开后在相同队位上查询进度仍报告相应结果。普通瓶流程保留原有状态、字段和读取语义。组成员的 `retrieve` 与 `recompute` 均返回 `None`。

以两份精确换算值的绝对差比较登记阈值，小于或等于阈值为 `pass`，大于阈值为 `over_limit`。组结果签发须有一次成功的持久提交，固定组回执、裁决和两份原样的关联。组判定、两份原样与组回执整体可领，超差也保留两份原样。

首瓶保全失败保留该瓶在办，原队位继续阻挡设备派发。组签发失败时组结果不可见，原组可按原物理完成续办，两瓶均不得重吸。末瓶在组签发失败期间继续占用设备。未提交保全或组签发时沿用 `commit_failed`。如果档案提交已成功而独立的队位确认失败，沿用 `checkpoint_failed`，续办可用已提交的证据补认。设备仍按原规则报告 `pending`、`source_read_failed` 和 `invalid_reading` 等状态。原已签成果保持可领。

## 组领取

`Workbench.retrieve_pair(pair_id)` 接受合法的组号，未知或未签的组返回 `None`，非法标识抛 `ValueError`。最终成果可按属性访问，含 `pair_id`、`batch_id`、`profile_id`、`threshold`、`difference`、`decision`、`receipt_id` 和 `members`。`threshold` 与 `difference` 均是 `Fraction`。`receipt_id` 是稳定的非空组回执字符串。`members` 按登记顺序给出两个对象，每个含 `order_id`、`bottle_id`、`completion_number`、`raw_code` 和 `measured`，其中 `measured` 沿用 `MeasuredValue`。断开设备并重开工作库后仍领取同一裁决、回执及两瓶出处。质控可用返回的定标引用和两份原码离线复算。

## 录制与核验

`examples/parallel_pair.json` 的两个场景各从一份独立的 `examples/shift.json` 录制起步。先按录制登记批次和定标，将原录制的 `orders` 作为 `members`，使用所选场景的组号和阈值登记。设备留样从 40/12 起，A 完成 41/17，B 完成 42/23。按 `3x/8 + 1/4` 手算分别为 53/8、71/8 NTU，相减的绝对值为 9/4 NTU。阈值 9/4 通过，阈值 2 超差，两个场景均可领取两份原样。

用 `automatic=False` 时，每瓶先 `advance` 排队，再调用两次 `device.tick()` 分别开工和完成，随后以 `resume` 或 `on_notices` 归集。可在 A 已保全后关闭并重开两个库。此时组与成员的正式领取均为空，B 尚未产生动作。续采 B 后断开设备，仅打开工作库领取组结果。重复登记和领取时检查队位、成员顺序和回执不变，检查异参、成员占用或非法登记后无新增工单。

沿用 `tests/test_publication.py` 的 SQLite 拒绝夹具做法。可以针对实现中对应的提交写入，设置测试专用触发器和延迟外键，使 SQLite 在首瓶保全或末瓶组签发的提交处真实拒绝。首瓶失败时留样仍为 41/17，B 不得发令。末瓶失败时组结果不可领，设备动作账已有 41 和 42。解除拒绝后重开并补办，检查两份原动作、原码和组回执，动作账仍只有这两次。

沿用 `.pointwise-runtime.json` 的清单格式。`version` 为 1，`kind` 为 `cli`，`setup` 是 argv 列表，`verify` 和 `demo` 各为一个 argv。`verify` 保留全部原回归，接通平行样联合核验并在失败时返回非零。`demo` 演示实际的首瓶保全、续采和断源按组领取。交付时简述用于确认放行和用于签发的持久依据。
