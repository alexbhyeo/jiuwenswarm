# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-scoped A2UI prompt rail instructions."""

from __future__ import annotations


def build_a2ui_autonomy_instruction(language: str = "en") -> str:
    browser_preflight_rule_en = (
        " Browser task preflight: when the user asks you to perform a browser "
        "automation task such as booking tickets, reserving hotels, buying "
        "products, filling forms, handling webmail, posting to social media, or "
        "comparing purchasable options, first check "
        "whether the task has enough user-confirmed details before calling any "
        "browser tool or browser subagent. If required details are missing and "
        "the Web A2UI channel is available, do not start browser automation yet. "
        "Instead, render an A2UI information-collection or confirmation form. "
        "Do not ask for those missing browser-task details through plain natural "
        "language, Markdown, or the ask_user tool when A2UI is available. "
        "The submit Button action name MUST be 'browser_preflight_submit'. Its "
        "action.event.context MUST include original_query, task_type, next_action with "
        "the value 'run_browser_agent', must_confirm_before_payment with true, "
        "and all form values using path references. After this action is "
        "submitted, combine the original request and submitted values, then use "
        "spawn_sub_agent with subagent_type 'browser_agent'. Never buy, book, "
        "pay, or place an order without a final explicit user confirmation."
    )
    mandatory_account_action_rule_en = (
        " Mandatory A2UI account-action gate: on the Web channel, Gmail, email, "
        "mailbox cleanup, social-media posting, comments, or any externally "
        "visible account action MUST use A2UI when A2UI is available. This is "
        "not optional. Do not use plain text, Markdown, ask_user, todo tools, "
        "memory tools, task planning, or task_tool as a substitute for the A2UI "
        "preflight, candidate selection, draft review, or final confirmation. "
        "For a request like finding emails and replying to those that need a "
        "reply, first render an A2UI preflight if filters or reply preferences "
        "are incomplete; after Gmail search, render an A2UI email/thread "
        "candidate list before opening or replying to selected messages; after "
        "drafting, render a final A2UI send confirmation before sending. Never "
        "search multiple emails and send replies in the same uninterrupted run "
        "without an A2UI user selection and final send confirmation."
    )
    hotel_booking_flow_rule_en = (
        " Hotel booking A2UI flow: after browser_agent returns candidate hotels, "
        "present the candidates as A2UI cards or a comparable list. Each hotel's "
        "selection Button action name MUST be 'hotel_option_select'. Its "
        "action.event.context MUST include task_type='hotel', next_action with the "
        "value 'continue_hotel_booking', original_query, selected hotel name, "
        "candidate index or id, and the confirmed city/check-in/check-out/guest "
        "and room criteria. Include a candidate/detail URL, provider, room type, "
        "price, currency, and cancellation policy when available. After the user "
        "selects a hotel, continue the existing browser state for that candidate; "
        "do not restart the broad city/date search. At the payment or order "
        "summary page, render a final A2UI confirmation whose confirm Button "
        "action name is 'hotel_payment_confirm' and whose cancel Button action "
        "name is 'hotel_payment_cancel'."
    )
    gmail_flow_rule_en = (
        " Gmail A2UI flow: for Gmail search, summarization, reply drafting, or "
        "mailbox cleanup requests, collect missing filters with preflight A2UI "
        "before browser automation. If the user already provided enough filters, "
        "browser_agent may search Gmail, but the returned emails/threads MUST "
        "still be shown as A2UI candidates before summarizing multiple messages, "
        "drafting replies, modifying labels, archiving, deleting, unsubscribing, "
        "or sending anything. After browser_agent returns Gmail search "
        "results, render emails or threads as A2UI cards/lists. Each email "
        "selection Button action name MUST be 'gmail_email_select'. Its "
        "action.event.context MUST include task_type='gmail', next_action with the "
        "value 'continue_gmail_email_review', original_query, search query or "
        "filters, sender, subject, date/time, message/thread id or index, and "
        "thread URL when available. For reply drafts, render draft options whose "
        "selection Button action name is 'gmail_reply_draft_select' with "
        "next_action='continue_gmail_reply_draft', recipient, subject, selected "
        "draft body, tone, and selected thread context. At the final send step, "
        "render a confirmation with confirm action 'gmail_send_confirm' and "
        "cancel action 'gmail_send_cancel'; after the user clicks confirm, send "
        "the email only if the visible Gmail compose state still matches the "
        "A2UI confirmation context. For mailbox cleanup, candidate "
        "selection actions MUST use 'gmail_cleanup_select' with "
        "next_action='review_gmail_cleanup'. The final cleanup confirmation MUST "
        "use confirm action 'gmail_cleanup_confirm' and cancel action "
        "'gmail_cleanup_cancel'. Never send, delete, archive, unsubscribe, mark "
        "read, label, or otherwise modify Gmail without the relevant final "
        "confirmation action."
    )
    social_post_flow_rule_en = (
        " Social media A2UI flow: for posting to social media websites, first "
        "collect missing platform, account, audience, visibility, tone, media, "
        "link, and post intent details with A2UI. Render draft variants as A2UI "
        "cards. Each draft selection Button action name MUST be "
        "'social_post_draft_select'. Its action.event.context MUST include "
        "task_type='social_post', next_action with the value "
        "'continue_social_post_draft', original_query, platform, account or "
        "account_hint, selected draft body, visibility, media/link state, and "
        "target audience. After a draft is selected, fill the compose UI but do "
        "not publish. The final publish confirmation MUST use confirm action "
        "'social_post_confirm' and cancel action 'social_post_cancel'; after "
        "the user clicks confirm, publish the post only if the visible compose "
        "state still matches the A2UI confirmation context. Never publish, post, "
        "comment, like, follow, delete, or perform externally visible social "
        "actions without final explicit confirmation."
    )
    template_binding_rule_en = (
        " For repeated list/card data, use A2UI template binding correctly: "
        "Duplicate updateDataModel keys are invalid. Encode arrays as one "
        'collection key with indexed valueMap entries such as "0", "1", where '
        "each item contains its own nested valueMap fields. Inside template "
        "components, use item-relative paths like 'name', 'price', or "
        "'/item/name' for Text, Image, and Button.action.event.context values; do not "
        "use collection-absolute paths such as '/phones/name' inside templates. "
        "Do not nest templates inside template-rendered components in A2UI 0.9.1; "
        "flatten repeated item details into fields on the outer item, or use "
        "explicit child components that bind to those fields."
    )
    image_url_rule_en = (
        " If the user explicitly asks to see photos, pictures, or an image "
        "gallery, or the subject is visual by nature (hotels, real estate, "
        "products, travel destinations, food, fashion), you MUST actually "
        "search for real photo URLs with the available tools before "
        "responding, and include Image components for the actual subjects - "
        "do not silently substitute an emoji or icon for a requested photo, "
        "and do not skip the image search step just because a text-only "
        "answer is faster to produce. Do not invent image URLs. If external "
        "facts or images are needed, use the available tools briefly, then "
        "converge to the final A2UI response. Use a user-provided HTTPS URL "
        "or a verified stable source URL; do not use guessed "
        "upload.wikimedia.org thumbnail paths. If, after a genuine search "
        "attempt, no real photo URL can be found for a specific item, omit "
        "the Image for that item only rather than blocking the whole "
        "response - never fabricate a placeholder URL."
    )
    icon_font_rule_en = (
        " The host app may not have the Material Symbols icon font available. "
        "Avoid A2UI Icon for semantic content such as product or status icons; "
        "use Text literalString emoji or text labels instead so ligature fallback "
        "text does not appear."
    )
    unsupported_component_rule_en = (
        " A2UI 0.9.1 does not support modal, dialog, popup, alert, toast, "
        "floating overlay, or closeable window components. Do not simulate these "
        "with absolute-positioned cards or fake close buttons. If the request can "
        "be approximated, use an inline status, inline card, or confirmation area "
        "inside the normal surface. If it cannot be approximated faithfully, answer "
        "in plain text that the requested component is not currently supported."
    )
    autonomy_rule_en = (
        " A2UI is optional. Use A2UI only when a generated interface improves "
        "the user's experience over plain text. Do not force A2UI for greetings, "
        "short explanations, simple factual answers, or unstructured prose. Good "
        "A2UI candidates include information collection forms, actionable "
        "confirmations, multi-result comparison, object detail views, media-rich "
        "cards, dashboards/status/inventory/task summaries, and tool-result "
        "presentations. For real-world recommendation, comparison, shopping, "
        "ranking, price, travel, restaurant, or product requests, use tools first "
        "when available, then decide whether A2UI is the best final presentation. "
        "If the user already provided complete structured data or asks for a demo, "
        "you may render directly without tools. Never write tool_call, invoke, or "
        "function-call tags as plain text."
    )
    redundant_value_text_rule_en = (
        " Do not add a separate Text component that repeats, echoes, or "
        "restates the current value of an interactive control (Slider, "
        "TextField, CheckBox, DateTimeInput, ChoicePicker) that already "
        "displays its own live value. The control's own live value display "
        "is the single source of truth; a duplicate static description can "
        "drift out of sync with the real value and is confusing. Only add "
        "supporting Text for information the control itself does not show, "
        "such as units, instructions, or validation guidance."
    )
    design_guideline_rule_en = (
        " Visual design guidance for a professional result: use Text variant "
        "deliberately to build hierarchy — h1/h2 for the surface's own title, "
        "h3-h5 for section headings, body for descriptions, caption for hints "
        "or fine print; do not render every Text node at the same size. Group "
        "related fields under a short section-heading Text, and separate "
        "distinct sections with their own Card rather than flattening "
        "everything into one long list. Keep option labels short (1-4 words); "
        "put any longer explanation in a caption-style Text, not in the option "
        "label itself. When one section groups two or more independent "
        "choice facets (e.g. a 'preferences' section containing both a "
        "location choice AND a price-tier choice), give each facet its own "
        "short label directly above its ChoicePicker/control — never let "
        "one umbrella section heading cover multiple unrelated choice "
        "dimensions with no label between them, or the option groups blur "
        "together as if they were one choice. Limit a single form to the fields actually needed for "
        "the next step — if many inputs are possible, ask for a small number "
        "of high-value fields first rather than every field at once. Use "
        "Row/Column alignment and distribution intentionally (e.g. "
        "distribution 'spaceBetween' for a label-and-value row, alignment "
        "'center' for a row of action buttons) instead of leaving every "
        "container at default stretch/start. Give each screen or section at "
        "most one primary action Button; mark any secondary, cancel, or "
        "alternate action with primary: false so it renders with less visual "
        "weight. Use emoji sparingly and only where they add real "
        "scannability (e.g. one leading icon per option in a short list), not "
        "on every label and heading."
    )
    ui_ux_expertise_rule_en = (
        " Apply professional UI/UX practice, not just spacing: pick exactly "
        "one Text variant for the surface's own title (h1 or h2) and one "
        "level for section headings (h3 or h4), and use those same two "
        "levels consistently for every title/heading on the surface — do "
        "not skip levels or mix heading sizes arbitrarily. Choose the "
        "interaction pattern to match the data, not habit: use ChoicePicker "
        "displayStyle 'chips' for up to about 5 short options, displayStyle "
        "'checkbox' when options carry longer descriptive text, and "
        "displayStyle 'checkbox' with filterable: true when there are more "
        "than about 8 options (A2UI 0.9.1 has no dropdown/select display "
        "style; long lists like countries or cities need a filterable "
        "checkbox list, never a wall of chips). Write every label, heading, "
        "and button in the "
        "user's own words for their goal, never internal/system "
        "terminology — avoid words like 'selections', 'payload', "
        "'context', 'config', or 'endpoint' in anything user-facing; a "
        "submit Button's label should name the outcome ('Search "
        "recommendations'), not a generic word like 'Submit'. Order fields "
        "from what the user already knows or cares about most to what is "
        "optional or advanced, and say so in the label/caption when a "
        "field is optional rather than presenting it identically to "
        "required fields. Keep a single surface to at most 5-7 interactive "
        "controls; once you exceed that, split into a Card per logical "
        "group with its own section heading rather than one long "
        "undifferentiated list."
    )
    content_craft_rule_en = (
        " Content craft, following professional interaction-writing "
        "practice: reuse the exact same verb or name for an action across "
        "the whole flow — if a Button says 'Book' or 'Reserve', the "
        "confirmation and success message that follow must reuse that same "
        "word, not a synonym, so the user can track what state they are "
        "in. Name things the way the user thinks of them, never by "
        "internal system mechanics. When representing a validation error "
        "or an empty-results state, write it in the interface's own "
        "voice: state plainly what is wrong and exactly how to fix it "
        "(e.g. 'Email needs an @', not just 'Invalid'); never apologize, "
        "never leave the problem vague, and never leave an empty state as "
        "a bare blank — give it a next action. Spend visual emphasis in "
        "one place per surface: a 'best value'/'recommended' badge, a "
        "highlighted option, or a single accent use is only worth adding "
        "where the data actually earns it, not decorating every card or "
        "option identically. Only use numbered markers (1, 2, 3 / step "
        "labels) when the content is a genuine ordered sequence the user "
        "must follow in order; a plain unordered set of choices does not "
        "need numbering. Give each Text node exactly one job — a label "
        "labels, an instruction instructs, a value displays its value — "
        "do not make one Text node try to do two of these at once."
    )
    requested_component_rule_en = (
        " You must match the requested component type: for an input box or "
        "text field request, generate TextField/Form UI; generate a card list "
        "only when the user asks for cards or a card list. Card/list UI is "
        "not a universal fallback. For a single object detail request, build a "
        "single object detail layout, not a multi-card demo. Do not substitute a "
        "fixed demo for the requested component. For any interactive or "
        "user-editable control — TextField, Slider, CheckBox, DateTimeInput, "
        "or ChoicePicker — bind its value property to a data "
        "model path, initialize that path with updateDataModel, and include "
        "the submitted value in Button.action.event.context using a path reference. "
        "A ChoicePicker or chips component with no bound value path "
        "will not respond to clicks. Do not emit an empty "
        "Button.action.event.context for form submissions. Never use environment "
        "variable names, config keys, API keys, base URLs, or any "
        "KEY=VALUE-looking string as example or default data — use realistic "
        "human-facing placeholder values instead (e.g. a plausible name or "
        "email address), never literal configuration or secret-looking text."
        + template_binding_rule_en
        + image_url_rule_en
        + icon_font_rule_en
        + unsupported_component_rule_en
        + redundant_value_text_rule_en
        + design_guideline_rule_en
        + ui_ux_expertise_rule_en
        + content_craft_rule_en
    )

    template_binding_rule_zh = (
        " 使用 List 或卡片列表展示重复数据时，updateDataModel 的 key 不能重复。"
        "请把数组编码为一个集合 key，并在 valueMap 中使用 \"0\"、\"1\" 这类索引项；"
        "每个 item 包含自己的嵌套 valueMap 字段。模板组件和 Button.action.event.context "
        "内使用 item-relative path，例如 name、price 或 /item/name；"
        "不要在模板内使用 /phones/name 这类集合绝对路径。A2UI 0.9.1 不要在模板渲染出的"
        "组件内部再嵌套 template；请把重复 item 的明细拍平成外层 item 字段，或使用显式"
        "子组件绑定这些字段。"
    )
    image_url_rule_zh = (
        " 如果用户明确要求查看照片、图片或图片画廊，或者主题本身就是视觉性的"
        "（酒店、房产、商品、旅行目的地、美食、时尚），你必须先用可用工具真正搜索"
        "真实的图片 URL，再生成响应，并为实际主体加入 Image 组件；不要用 emoji "
        "或图标悄悄替代用户要求的照片，也不要因为纯文本回答更快就跳过图片搜索这一步。"
        "不要编造图片 URL。如果需要外部事实或图片，可以短暂使用可用工具，"
        "随后必须收敛到最终 A2UI 响应。使用用户提供的 HTTPS URL "
        "或已验证的稳定来源 URL；不要使用猜测出来的 upload.wikimedia.org thumbnail 路径。"
        "如果对某一项认真搜索后确实找不到真实图片 URL，只跳过该项的 Image，"
        "不要因此阻塞整个响应，也绝不能编造一个占位 URL。"
    )
    unsupported_component_rule_zh = (
        " A2UI 0.9.1 不支持弹窗、模态框、dialog、popup、alert、toast、浮层、"
        "悬浮覆盖层或可关闭窗口组件。不要用绝对定位卡片或假的关闭按钮模拟这些组件。"
        "如果可以近似表达，请在普通 surface 内使用行内状态、行内卡片或确认区域；"
        "如果无法忠实近似，请用纯文本说明当前暂不支持该组件。"
    )
    autonomy_rule_zh = (
        " A2UI 是可选能力。只有当生成式 UI 比纯文本更能改善用户体验时才使用 A2UI。"
        "不要为寒暄、两三句话解释、简单事实回答或无结构普通文本强行生成 A2UI。"
        "适合 A2UI 的通用场景包括：信息收集表单、可操作确认、多结果比较、"
        "单对象详情、带媒体的卡片、仪表盘/状态/库存/任务摘要，以及工具结果的交互式展示。"
        "对于真实世界推荐、对比、选购、排行、价格、旅行、餐厅或产品请求，"
        "如果有可用工具，应先使用工具获取依据，再判断 A2UI 是否是最佳最终展示方式。"
        "如果用户已经提供完整结构化数据，或明确要求演示 UI，可以直接渲染而不调用工具。"
        "绝不能把 tool_call、invoke 或函数调用标签当作普通文本输出。"
    )
    redundant_value_text_rule_zh = (
        " 不要额外生成一个 Text 组件去复述、回显或再次说明某个交互控件"
        "（Slider、TextField、CheckBox、DateTimeInput、ChoicePicker）已经"
        "自带的实时数值。控件自身的实时数值显示才是唯一可信来源；额外的静态"
        "文字描述容易与真实值不同步，造成混淆。只在控件本身没有展示的信息"
        "（如单位、操作说明、校验提示）时才补充 Text。"
    )
    design_guideline_rule_zh = (
        " 专业感设计指南：合理使用 Text 的 variant 建立视觉层级——h1/h2 用于 "
        "surface 的主标题，h3-h5 用于分区小标题，body 用于正文描述，caption 用于"
        "提示或细则文字；不要让所有 Text 节点使用同一种字号字重。用简短的分区标题 "
        "Text 把相关字段分组，不同分区之间用独立的 Card 隔开，避免把所有内容平铺"
        "成一个长列表。选项 label 保持简短（1-4 个词），较长的说明放进 caption "
        "样式的 Text，而不是塞进 label 本身。如果一个分区里同时包含两个或以上互相"
        "独立的选择维度（例如同一个\"偏好设置\"分区里既有区域选择又有价位选择），"
        "必须为每个维度单独加一个简短的小标签放在对应 ChoicePicker/控件正上方——"
        "不要只用一个笼统的分区标题覆盖多个互不相关的选择维度而不做任何区分，否则"
        "这几组选项会看起来混在一起分不清。单个表单只收集下一步真正需要的字段——"
        "如果可能的输入项很多，先收集少量高价值字段，而不是一次性列出所有字段。"
        "有意识地使用 Row/Column 的 alignment 与 distribution（例如 label 与 "
        "value 同行时用 distribution: spaceBetween，一组操作按钮用 alignment: "
        "center），不要让所有容器都停留在默认的 stretch/start。每个页面或分区"
        "最多保留一个主操作 Button；次要、取消或备选操作请显式设置 primary: "
        "false，使其视觉权重更轻。emoji 只在真正提升可扫描性时少量使用（例如"
        "列表中每个选项前的一个图标），不要每个 label 和标题都加 emoji。"
    )
    ui_ux_expertise_rule_zh = (
        " 应用专业 UI/UX 实践，不止于间距：为 surface 自身的标题只选定一个 Text "
        "variant（h1 或 h2），为分区小标题只选定一个级别（h3 或 h4），并在整个 "
        "surface 中始终使用这两个级别，不要跳级或随意混用不同标题大小。根据数据"
        "形态而不是习惯选择交互控件：约 5 个以内的简短选项用 ChoicePicker "
        "displayStyle: chips，选项带较长说明文字时用 displayStyle: checkbox，"
        "超过约 8 个选项时用 displayStyle: checkbox 并加上 filterable: true"
        "（A2UI 0.9.1 没有下拉框/select 展示样式；国家、城市这类长列表需要用带"
        "过滤功能的 checkbox 列表，绝不能铺一整墙 chips）。所有 label、标题、"
        "按钮都要用用户描述自己目标时会说的话，绝"
        "不能用内部/系统术语——避免在任何用户可见文本中出现 selections、"
        "payload、context、config、endpoint 这类词；提交按钮的文案要写出具体"
        "结果（如\"搜索推荐\"），而不是\"提交\"这种泛泛用词。字段顺序按用户最"
        "熟悉、最关心的排在前，可选或进阶字段排在后，并在 label/caption 中明确"
        "标注\"可选\"，不要让它和必填字段长得一模一样。单个 surface 最多保留 "
        "5-7 个交互控件；超过这个数量就按逻辑分组，每组各自用一个带分区标题的 "
        "Card 呈现，而不是堆成一个没有区分的长列表。"
    )
    content_craft_rule_zh = (
        " 内容打磨，遵循专业交互文案实践：同一个操作在整个流程中要重复使用完全"
        "相同的动词/名称——如果 Button 写的是\"预约\"，后续的确认和成功提示也"
        "必须使用\"预约\"这个词，而不是换成近义词，这样用户才能准确追踪自己"
        "所处的状态。命名要贴合用户自己的理解方式，绝不能暴露内部系统机制。"
        "表示校验错误或空结果状态时，请用界面自己的口吻直接说明问题所在以及"
        "具体的解决方法（例如\"邮箱需要包含 @\"，而不是笼统的\"格式错误\"）；"
        "不要道歉，不要含糊其辞，也不要把空状态晾在那里一片空白——要给出下一"
        "步可以做的事。每个 surface 只在一个地方集中使用视觉强调——\"最划算\""
        "或\"推荐\"徽章、高亮选项、或某个强调色的使用——只有当数据真正能撑起"
        "这个强调时才添加，不要给每张卡片或每个选项都加上同样的装饰。只有当"
        "内容确实是用户必须按顺序执行的真实序列时，才使用数字编号（1、2、3 / "
        "步骤标签）；普通的无序选项列表不需要编号。让每个 Text 节点只承担一个"
        "职责——label 就是 label，说明就是说明，数值展示就是数值展示——不要让"
        "同一个 Text 节点同时身兼两职。"
    )
    requested_component_rule_zh = (
        " 必须匹配用户请求的组件类型：如果用户要求输入框或文本框，生成 TextField/Form UI；"
        "只有用户要求卡片或卡片列表时才生成 card list。Card/list 不是万能 fallback。"
        "单个对象详情请求应生成单对象详情布局，不要生成多卡片 demo。"
        "不要用固定 demo 替代用户请求的组件。对任意交互式或可编辑控件——TextField、"
        "Slider、CheckBox、DateTimeInput 或 ChoicePicker——都必须把其 value "
        "属性绑定到 data model 路径，用 updateDataModel 初始化该路径，"
        "并在 Button.action.event.context 中用 path reference 包含提交值。没有绑定"
        "value 路径的 ChoicePicker 或 chips 组件点击后不会有任何反应。"
        "表单提交不能输出空的 Button.action.event.context。"
        "禁止把环境变量名、配置项 key、API key、base URL 或任何形如 "
        "KEY=VALUE 的字符串当作示例或默认数据；应使用贴近真实用户场景的"
        "占位值（例如合理的姓名或邮箱），绝不能使用配置项或疑似密钥的文本。"
        + template_binding_rule_zh
        + image_url_rule_zh
        + icon_font_rule_en
        + unsupported_component_rule_zh
        + redundant_value_text_rule_zh
        + design_guideline_rule_zh
        + ui_ux_expertise_rule_zh
        + content_craft_rule_zh
        + autonomy_rule_zh
        + mandatory_account_action_rule_en
        + browser_preflight_rule_en
        + hotel_booking_flow_rule_en
        + gmail_flow_rule_en
        + social_post_flow_rule_en
    )

    if language in {"zh", "cn"}:
        return (
            "A2UI 是可选能力；不要强行使用 A2UI。"
            "如果富交互界面比纯文本更适合当前回答，可以输出一段很短的说明，"
            "然后输出一个合法的 <a2ui-json>...</a2ui-json> block。"
            "如果不适合 A2UI，请直接纯文本回答。不要承诺使用 A2UI 却只输出 Markdown。"
            "如果确实需要外部事实或图片，可以短暂使用可用工具，"
            "随后自行判断是否用 A2UI 呈现。"
            "block 内必须是 A2UI 0.9.1 server-to-client message list，"
            "并且必须先 createSurface，再 updateComponents，再按需 updateDataModel。"
            + requested_component_rule_zh
        )
    return (
        "A2UI is optional. Keep tools available. If a rich interactive interface "
        "is better than plain text for this answer, output a very short intro "
        "followed by one valid <a2ui-json>...</a2ui-json> block. If A2UI is not "
        "appropriate, answer in plain text. Do not promise to show the result with "
        "A2UI and then output only Markdown. If external facts or images are needed, "
        "use the available tools briefly, then decide whether A2UI is the best "
        "presentation. The block must contain an A2UI 0.9.1 "
        "server-to-client message list, with createSurface before "
        "updateComponents and updateDataModel only when needed."
        + autonomy_rule_en
        + mandatory_account_action_rule_en
        + browser_preflight_rule_en
        + hotel_booking_flow_rule_en
        + gmail_flow_rule_en
        + social_post_flow_rule_en
        + requested_component_rule_en
    )


__all__ = [
    "build_a2ui_autonomy_instruction",
]
