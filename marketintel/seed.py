def seed(store):
    if store.meta('seed_version'):
        return
    projects = [
        ('web-agency', '建站服务商开工资料', '独立建站服务商 / 小型工作室', '收齐客户文案和图片，确认项目可以开工', 'client content collection website agency',
         '服务商', '一个获确认的开工资料包', '独立建站服务商社区与公开项目交流', '邮件、Google Docs、Content Snare',
         '垂直项目模板与资料就绪流程是否有额外价值仍需验证'),
        ('shopify-csv', 'Shopify 供应商商品表整理', '商品录入 / 店铺迁移服务商', '把特定供应商表整理成可测试导入的商品文件', 'Shopify supplier CSV variants import',
         '反复录入商品的服务商', '一份可测试导入的商品文件及修改记录', 'Shopify 官方社区及迁移服务商', '原生导入、Google Sheets、Matrixify',
         '同一种供应商格式反复出现时才考虑产品化；优先按次交付'),
        ('bakery-quote', '家庭烘焙定制报价', '持续接单的家庭烘焙者', '把原料、人工和包装成本转成可发送的报价', 'custom cookie pricing quote recipe cost',
         '实际接定制订单的烘焙者', '成本明细及不同数量的定制报价单', '烘焙社区、工具页面与报价模板内容', '免费计算器、表格、Cake Cost、Craftybase',
         '成本计算有免费竞争；保存配方与报价版本的付费需求待验证')]
    for slug, name, audience, task, keywords, payer, deliverable, channel, alternatives, hypothesis in projects:
        project_id = 'project-' + slug
        store.save('project', name, data={'audience': audience, 'task': task, 'keywords': keywords, 'region': '海外英文市场；首轮地区待选',
                                         'language': 'en', 'notes': '来自 2026-09-27 的公开资料调研。未完成付款验证。'}, identifier=project_id)
        store.save('opportunity', name, project_id, {'stage': 'needs-evidence', 'payer': payer, 'deliverable': deliverable,
                    'channel': channel, 'alternatives': alternatives, 'hypothesis': hypothesis,
                    'unknowns': '实际任务频率、替换原因、成交、交付成本、重复使用、搜索量', 'evidence_ids': [], 'economics': {}}, 'opportunity-' + slug)
    sources = [
        ('content-snare', 'Content Snare 定价', 'web-agency', 'https://contentsnare.com/pricing/'),
        ('matrixify', 'Matrixify 定价', 'shopify-csv', 'https://matrixify.app/pricing/'),
        ('shopify-docs', 'Shopify 原生导入问题', 'shopify-csv', 'https://help.shopify.com/en/manual/products/import-export/common-import-issues'),
        ('craftybase', 'Craftybase 免费烘焙计算器', 'bakery-quote', 'https://craftybase.com/cake-pricing-calculator')]
    for slug, name, project, url in sources:
        store.save('source', name, 'project-' + project, {'url': url, 'type': 'web', 'interval_hours': 336,
                    'capture_allowed': False, 'save_raw': True, 'tracking': False, 'policy': '历史演示来源；使用者需先核查允许的采集范围'}, 'source-' + slug)
    evidence = [
        ('content-price', 'Content Snare 历史标价与已有功能', 'web-agency', 'https://contentsnare.com/pricing/',
         '2026-09-27 调研记录：Basic 按月 42 美元/月，按年折合 35 美元/月；已有提醒、审批和门户。请复核当前网页。', 'fact', 'counter'),
        ('content-pain', '客户资料散落多个渠道', 'web-agency', 'https://www.reddit.com/r/webdesign/comments/1u1dbni/how_do_you_collect_website_content_from_clients/',
         '发帖者自述资料散落邮件、Drive 和聊天。帖子含意愿探询，独立性有限，不是付费证据。', 'self-report', 'support'),
        ('matrix-price', 'Matrixify 历史价格', 'shopify-csv', 'https://matrixify.app/pricing/',
         '2026-09-27 调研记录：有免费 Demo，Basic 20 美元/30 天。不能据此推断你的产品可成交。', 'fact', 'counter'),
        ('csv-solved', '导入困难的用户后来找到替代方案', 'shopify-csv', 'https://community.shopify.com/t/best-way-to-import-data-into-shopify/418231',
         '原帖抱怨原生导入与 Matrixify 模板，后来表示通过 Google Sheets 与 Altera 解决。应保留反证。', 'self-report', 'counter'),
        ('cookie-pain', '曲奇卖家的定价困惑', 'bakery-quote', 'https://www.reddit.com/r/Baking/comments/1ohwta3/cookie_pricing_help/',
         '用户自述需要考虑材料、时间和包装，但未证明愿意购买软件。', 'self-report', 'support'),
        ('cake-free', '免费烘焙计算工具已存在', 'bakery-quote', 'https://craftybase.com/cake-pricing-calculator',
         'Craftybase 有免费烘焙定价计算器，通用成本计算不是空白。', 'fact', 'counter')]
    for slug, name, project, url, summary, claim, direction in evidence:
        store.save('evidence', name, 'project-' + project, {'url': url, 'summary': summary, 'claim_type': claim, 'direction': direction,
                    'review_status': 'pending', 'observed_at': '2026-09-27', 'origin': '历史调研导入', 'resolved': 'yes' if slug == 'csv-solved' else 'unknown'}, 'evidence-' + slug)
    for slug, _, _, _, _, _, _, _, _, _ in projects:
        opp = store.get('opportunity-' + slug)
        data = opp['data']; data['evidence_ids'] = [e['id'] for e in store.list('evidence', 'project-' + slug)]
        store.save('opportunity', opp['name'], opp['project_id'], data, opp['id'])
    for slug, name, project, url, price, billing in [
        ('content-snare', 'Content Snare', 'web-agency', 'https://contentsnare.com/', 42, 'USD / 月付月价'),
        ('matrixify', 'Matrixify', 'shopify-csv', 'https://matrixify.app/', 20, 'USD / 30 天'),
        ('craftybase', 'Craftybase', 'bakery-quote', 'https://craftybase.com/', None, '免费计算器；付费价格未录入')]:
        store.save('competitor', name, 'project-' + project, {'url': url, 'price': price, 'billing': billing, 'currency': 'USD',
                    'checked_at': '2026-09-27', 'price_status': '历史公开标价；需复核', 'features': '请查证官方产品页与当前版本',
                    'evidence_ids': [e['id'] for e in store.list('evidence', 'project-' + project) if e['data'].get('claim_type') == 'fact']}, 'competitor-' + slug)
    store.meta('seed_version', 1)
