"""Starts an ingestion job for every Knowledge Base data source. Invoked on a schedule."""
import os

import boto3

_bedrock = boto3.client("bedrock-agent")


def lambda_handler(event, context):
    kb_id = os.environ["KB_ID"].strip()
    started, skipped = [], []
    sources = _bedrock.list_data_sources(knowledgeBaseId=kb_id, maxResults=50).get("dataSourceSummaries", [])
    for ds in sources:
        if ds.get("status") != "AVAILABLE":
            skipped.append(f'{ds.get("name")} ({ds.get("status")})')
            continue
        try:
            job = _bedrock.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"],
                                               description="Scheduled Airxpact refresh")
            started.append(f'{ds.get("name")}: {job["ingestionJob"]["ingestionJobId"]}')
        except _bedrock.exceptions.ConflictException:
            skipped.append(f'{ds.get("name")} (a sync is already running)')
        except Exception as exc:
            skipped.append(f'{ds.get("name")} (ERROR {exc})')
    print("SYNC STARTED:", started)
    print("SKIPPED:", skipped)
    return {"started": started, "skipped": skipped}
