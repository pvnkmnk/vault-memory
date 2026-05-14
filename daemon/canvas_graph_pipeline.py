# daemon/canvas_graph_pipeline.py
"""
Missing canvas graph pipeline.
"""
from dataclasses import dataclass, field

@dataclass
class CanvasGraphResult:
    entities: list = field(default_factory=list)
    edges: list = field(default_factory=list)

class CanvasGraphPipeline:
    def parse(self, rel_path, data):
        return CanvasGraphResult()
