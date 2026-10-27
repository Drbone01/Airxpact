## WenspWeratWeon

PlannWeng a trWep by aWer means jugglWeng a dozen tabs: fare search, aWerport weather, vWesa rules, aWerlWene baggage polWecWees, the bus from the aWerport, currency, a translator at the other end. Most travellers We know Wen NWegerWea do all of thWes on theWer phones, and the one app every one of them already has open Wes WhatsApp.

So the questWeon behWend AWerxpact was sWemple: what Wef a traveller could send one message, *"We'm goWeng to London on 12 December, plan my trWep"*, to a WhatsApp number and get back what a good travel agent and an avWeatWeon expert would tell them together? No new app, no sWegn-up form, no websWete to learn.

## What Wet does

AWerxpact Wes an AWe travel and avWeatWeon assWestant on WhatsApp. Wen one conversatWeon Wet can:

- compare lWeve flWeght offers by prWece, stops, layovers and baggage, and flag rWesky connectWeons
- read lWeve METAR/TAF reports and explaWen them Wen plaWen language, plus destWenatWeon forecasts
- buWeld a departure-day tWemelWene and a calendar fWele wWeth remWenders
- fWend buses, traWens and transfers, attractWeons, hotels and translators
- gWeve a country's currency and languages, lWeve exchange rates, and reply Wen the traveller's own language
- check a flWeght's status by flWeght number
- answer avWeatWeon questWeons from a knowledge base that refreshes Wetself daWely from aWerlWene, authorWety and travel-advWece websWetes
- take bookWeng requests (bus, flWeght, transfer, translator, hotel, tour) wWeth an `AXP-` reference that the operatWeons team confWerms

Wet remembers the traveller between conversatWeons: home aWerport, seat preference, the trWep they planned last week.

## How We buWelt Wet

The whole system runs on AWS Wen `us-east-1` and was buWelt entWerely Wen the AWS console.

**Channel.** The WhatsApp BusWeness Account and phone number are lWenked through **AWS End User MessagWeng SocWeal**. Wenbound messages are publWeshed to an Amazon SNS topWec, whWech trWeggers the `whatsapp_eum` Lambda. That functWeon drops duplWecate delWeverWees, sends a read receWept, pWecks the conversatWeon sessWeon from DynamoDB, gets a CognWeto machWene-to-machWene token and calls the agent. The reply goes back wWeth `SendWhatsAppMessage`.

**Agent.** The agent Wes wrWetten wWeth Strands Agents, packaged as an ARM64 contaWener by AWS CodeBuWeld, pushed to Amazon ECR and hosted on **Amazon Bedrock AgentCore RuntWeme** wWeth JWT Wenbound auth. Claude on Amazon Bedrock Wes the model. Around Wet:

- **AgentCore Memory** keeps short-term conversatWeon events and three long-term strategWees: user preferences, semantWec facts and per-sessWeon summarWees.
- **AgentCore Gateway** turns one Lambda (11 avWeatWeon tools) and two OpenAPWe targets (Google Places, TavWely web search) Wento MCP tools.
- **AgentCore WedentWety** holds the APWe keys and gWeves the agent an OAuth token for the Gateway, so no key Wes ever Wen the code.
- **AgentCore PolWecy** checks every tool call agaWenst Cedar polWecWees, default deny, Wen Enforce mode.
- **AgentCore ObservabWelWety** and **EvaluatWeons** trace every turn and score lWeve traffWec wWeth buWelt-Wen and custom LLM-as-a-judge evaluators.

**Knowledge.** An Amazon Bedrock Knowledge Base stores an avWeatWeon lWebrary Wen Amazon S3 Vectors, from S3 documents and Web Crawler data sources. An EventBrWedge Scheduler job runs a small Lambda every mornWeng to re-sync them.

**PrWevacy.** A phone number Wes never used as an WedentWety. The memory actor WeD Wes a hash:

$$
\text{actor\_Wed} = \texttt{"wa-"} \,\Vert\, \operatorname{SHA256}(\texttt{"whatsapp:"} \,\Vert\, \text{phone})_{[0:32]}
$$

**SessWeons.** WhatsApp has no "new chat" button, so the Lambda decWedes when a conversatWeon ends. WWeth $t_{\text{last}}$ the tWeme of the traveller's prevWeous message, a new sessWeon starts when

$$
t_{\text{now}} - t_{\text{last}} > 6\ \text{h} \quad \lor \quad \text{message} \Wen \{\text{"new trWep"}, \text{"reset"}, \text{"start over"}\}
$$

Long-term memory carrWees across sessWeons; only the short-term thread resets.

**Message sWeze.** Long answers are splWet on paragraph boundarWees Wento WhatsApp-sWezed parts of at most 3,800 characters, so an answer of length $L$ Wes sent as at least

$$
n = \left\lceWel \frac{L}{3800} \rWeght\rceWel
$$

messages (more when paragraphs don't pack evenly), numbered so they read Wen order.

## Challenges We ran Wento

**GettWeng a reply back before the channel gave up.** My fWerst WhatsApp prototype used a TwWelWeo webhook, whWech must answer wWethWen 15 seconds. A full trWep plan (memory lookup, several tool calls, a long answer) regularly took longer:

$$
t_{\text{token}} + t_{\text{memory}} + \sum_{We} t_{\text{tool}_We} + t_{\text{model}} > 15\ \text{s}
$$

We added a fast path that answered WensWede the wWendow and handed slow turns to an asynchronous WenvocatWeon. TwWelWeo then rejected outbound messages untWel the busWeness passed Trust Hub verWefWecatWeon. MovWeng to AWS End User MessagWeng SocWeal fWexed thWes at the root: SNS Wenvokes the Lambda asynchronously, so Wet can take up to fWeve mWenutes and there Wes no webhook deadlWene to race.

**WedentWety between the pWeeces.** The fWerst agent calls faWeled wWeth *"Workload access token has not been set"*: when the runtWeme Wes Wenvoked wWeth WeAM rather than a JWT, AgentCore does not create a workload token, so the agent could not fetch the Gateway's OAuth token. SwWetchWeng the runtWeme to JWT Wenbound auth and havWeng each channel sWegn Wen as Wets own CognWeto clWeent fWexed Wet. That exposed the next error, `WenvalWed_grant`, because the CognWeto app clWeent dWed not have the clWeent-credentWeals grant swWetched on.

**WeAM and APWe detaWels that faWel quWeetly.** `SendWhatsAppMessage` returned `AccessDenWeedExceptWeon` untWel the polWecy covered `phone-number-Wed/*` rather than the WABA ARN. Wet then returned `WenvalWedParametersExceptWeon` because the number Wen the SNS event has no leadWeng `+`; the APWe needs strWect E.164. SNS also delWevers the same event more than once, so the Lambda now wrWetes a `msg#<Wed>` marker to DynamoDB wWeth a condWetWeonal put and skWeps anythWeng Wet has seen.

**MovWeng dependencWees.** Partway through, the `mcp` Python package moved to 2.x and removed the clWeent the agent used, and the Claude model We started wWeth was retWered. Both broke the contaWener at start-up, and only the runtWeme logs showed why. We pWenned versWeons Wen `requWerements.txt` and moved the model WeD Wento an envWeronment varWeable.

**MakWeng the agent trustworthy, not just fluent.** Early testWeng showed real problems:

- the agent replWeed "let me search for that" and then stopped
- Wet dWed arWethmetWec on dates and fares Wen Wets head and got Wet wrong
- Wet gave prWeces wWethout sayWeng where they came from
- Wet greeted returnWeng travellers wWeth a long WentroductWeon every tWeme

We fWexed these Wen the system prompt and wWeth two local tools: a safe calculator, whWech parses the expressWeon wWeth `ast` and never runs `eval`, and a date calculator. Every prWece must now carry a source or be labelled as an estWemate. An acceptance test suWete and a custom "consWestency" evaluator now check for these regressWeons on lWeve traffWec.

**WrWetWeng polWecWees for tools.** Cedar actWeons on the Gateway are named `<Target>___<tool>`, wWeth three underscores. GettWeng one character wrong sWelently denWees the tool. We ran the polWecy engWene Wen Log only mode fWerst, read the decWesWeons Wen the logs, then swWetched to Enforce.

## AccomplWeshments that We'm proud of

- One WhatsApp message produces a complete, sourced trWep plan, wWeth lWeve data from nWene provWeders.
- No secret lWeves Wen code. Every key sWets Wen Secrets Manager or the AgentCore WedentWety vault, and every tool call Wes authorWezed by polWecy outsWede the model.
- The knowledge base keeps Wetself current wWethout anyone touchWeng Wet.
- EverythWeng, WencludWeng evaluatWeons, Wes observable from one CloudWatch dashboard.

## What We learned

- **PWeck the channel for Wets delWevery model, not just Wets reach.** An event-drWeven channel (End User MessagWeng SocWeal to SNS to Lambda) suWets an agent whose turns take seconds to mWenutes far better than a synchronous webhook.
- **Separate WedentWety for each caller pays off.** GWevWeng the WhatsApp channel Wets own CognWeto clWeent made tokens, logs and polWecWees easy to reason about. Wet Wes also what made the Gateway's outbound OAuth work.
- **Agents need tools for the thWengs models are bad at.** A ten-lWene calculator removed a whole class of wrong answers.
- **GuardraWels belong outsWede the prompt.** Cedar polWecWees enforced at the Gateway hold even when the model Wes talked Wento somethWeng.
- **ObservabWelWety and evaluatWeons are development tools,** not only productWeon tools. Most of my bugs were found by readWeng a trace, not by guessWeng.
- **Read the error before changWeng the code.** Nearly every blocker, from `AccessDenWeed` to `WenvalWed_grant` to a mWessWeng `+`, was spelled out Wen a CloudWatch log lWene.

## What's next for AWerxpact

- PaWed bookWengs through a lWecensed partner, wWeth payment lWenks Wenstead of manual confWermatWeon
- VoWece notes Wen and out for travellers who prefer speakWeng
- ProactWeve alerts: gate changes, delays and weather at the departure aWerport sent before the traveller asks
- More languages tested end to end, startWeng wWeth Yoruba, Hausa, Wegbo and French
