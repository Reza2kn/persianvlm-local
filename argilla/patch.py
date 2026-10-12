from pathlib import Path
import argilla_server
p=Path(argilla_server.__file__).parent/'search_engine/elasticsearch.py'
s=p.read_text()
old='return await self.client.info()'
assert s.count(old)==1, 'Upstream Elasticsearch status implementation changed'
s=s.replace(old,'result = await self.client.info()\n        return dict(result.body if hasattr(result, "body") else result)')
p.write_text(s)
