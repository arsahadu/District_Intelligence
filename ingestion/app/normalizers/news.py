import hashlib
from datetime import datetime

from app.models.common_record import CommonRecord, Location
from app.models.source_article import SourceArticle


def article_to_common_record(
    article: SourceArticle,
    index: int
) -> CommonRecord:
    digest = hashlib.sha1(article.url.encode("utf-8")).hexdigest()[:16]

    return CommonRecord(

        record_id=f"NEWS-MDU-{digest}",

        source_id="dinamalar",

        source_type="news",

        record_type="article",

        title=article.title,

        event_time=article.published_at,

        location=Location(
            raw_text="Madurai",
            district="Madurai",
            state="Tamil Nadu"
        ),

        data={
            "content": article.content,
            "language": "ta"
        },

        source_url=article.url,

        retrieved_at=datetime.now().isoformat(),

        raw_reference=article.url
    )