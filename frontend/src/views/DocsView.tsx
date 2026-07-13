import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Stack,
  Tab,
  Tabs,
  Typography,
  useMediaQuery
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { ArrowBack, MenuBookOutlined } from "@mui/icons-material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DocPageInfo, getDocPage, listDocPages } from "../api";

type DocsViewProps = {
  onBack?: () => void;
  backLabel?: string;
};

type DocSegment = { type: "markdown" | "mermaid"; content: string };

// Mermaid blocks are rendered by mermaid itself; everything between them goes
// through react-markdown. Splitting up front avoids fighting react-markdown's
// pre/code nesting for custom fence renderers.
function splitSegments(markdown: string): DocSegment[] {
  const segments: DocSegment[] = [];
  const fence = /```mermaid\n([\s\S]*?)```/g;
  let cursor = 0;
  for (let match = fence.exec(markdown); match; match = fence.exec(markdown)) {
    if (match.index > cursor) {
      segments.push({ type: "markdown", content: markdown.slice(cursor, match.index) });
    }
    segments.push({ type: "mermaid", content: match[1] });
    cursor = match.index + match[0].length;
  }
  if (cursor < markdown.length) {
    segments.push({ type: "markdown", content: markdown.slice(cursor) });
  }
  return segments;
}

let mermaidSequence = 0;

function MermaidDiagram({ code }: { code: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    // A fresh id per invocation: StrictMode double-runs this effect, and two
    // concurrent mermaid.render calls sharing an element id can wedge.
    const renderId = `docs-mermaid-${(mermaidSequence += 1)}`;
    void (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "dark",
          securityLevel: "strict",
          // Pure SVG labels: the docs page styles paragraphs (line-height,
          // font), which bleeds into HTML labels inside foreignObjects and
          // clips the last line of multi-line nodes. Mermaid 11 only honors
          // this when the top-level key is set alongside the flowchart one.
          htmlLabels: false,
          flowchart: { htmlLabels: false },
          fontFamily: '"trebuchet ms", verdana, arial, sans-serif'
        } as Parameters<typeof mermaid.initialize>[0]);
        const rendered = await mermaid.render(renderId, code);
        if (active) {
          setSvg(rendered.svg);
        }
      } catch (err) {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to render diagram.");
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [code]);

  if (error) {
    return (
      <Alert severity="warning" variant="outlined" sx={{ my: 2 }}>
        Diagram failed to render: {error}
      </Alert>
    );
  }
  if (!svg) {
    return (
      <Box display="flex" justifyContent="center" py={4}>
        <CircularProgress size={22} />
      </Box>
    );
  }
  return (
    <Box
      sx={{
        my: 2,
        p: { xs: 1, sm: 2 },
        borderRadius: 2,
        border: "1px solid rgba(255,255,255,0.08)",
        backgroundColor: "rgba(10,11,17,0.6)",
        overflowX: "auto",
        "& svg": { maxWidth: "100%", height: "auto" }
      }}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

// Relative links between docs pages ("ARCHITECTURE.md", "PRODUCTION_READINESS.md")
// switch the in-app page instead of navigating away.
const DOC_LINK_TO_PAGE: Record<string, string> = {
  "ARCHITECTURE.md": "architecture",
  "FLOW_VARIANTS_AND_ARCHITECTURE.md": "variants",
  "PRODUCTION_READINESS.md": "production-readiness"
};

export function DocsView({ onBack, backLabel = "Back" }: DocsViewProps) {
  const [pages, setPages] = useState<DocPageInfo[]>([]);
  const [pageId, setPageId] = useState("architecture");
  const [markdown, setMarkdown] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("sm"));

  useEffect(() => {
    void listDocPages()
      .then(setPages)
      .catch(() => setPages([]));
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    void getDocPage(pageId)
      .then((doc) => {
        if (active) {
          setMarkdown(doc.markdown);
        }
      })
      .catch((err) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Failed to load documentation.");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [pageId]);

  const segments = useMemo(() => (markdown ? splitSegments(markdown) : []), [markdown]);

  const markdownSx = {
    "& h1": { fontSize: isMobile ? "1.6rem" : "2rem", fontWeight: 750, mt: 1, mb: 1.5 },
    "& h2": {
      fontSize: isMobile ? "1.25rem" : "1.5rem",
      fontWeight: 700,
      mt: 3.5,
      mb: 1,
      pb: 0.5,
      borderBottom: "1px solid rgba(255,255,255,0.1)"
    },
    "& h3": { fontSize: isMobile ? "1.05rem" : "1.2rem", fontWeight: 700, mt: 2.5, mb: 1 },
    "& p, & li": { lineHeight: 1.65, color: "rgba(255,255,255,0.86)" },
    "& a": { color: "#82b1ff" },
    "& code": {
      fontFamily: "monospace",
      fontSize: "0.85em",
      backgroundColor: "rgba(255,255,255,0.08)",
      borderRadius: 0.5,
      px: 0.5,
      py: 0.1
    },
    "& pre": {
      backgroundColor: "rgba(10,11,17,0.8)",
      border: "1px solid rgba(255,255,255,0.08)",
      borderRadius: 1.5,
      p: 1.5,
      overflowX: "auto"
    },
    "& pre code": { backgroundColor: "transparent", p: 0 },
    "& table": {
      borderCollapse: "collapse" as const,
      display: "block",
      overflowX: "auto",
      my: 1.5
    },
    "& th, & td": {
      border: "1px solid rgba(255,255,255,0.14)",
      px: 1.25,
      py: 0.75,
      textAlign: "left" as const,
      fontSize: "0.875rem"
    },
    "& th": { backgroundColor: "rgba(255,255,255,0.05)" },
    "& blockquote": {
      borderLeft: "3px solid rgba(130,177,255,0.5)",
      ml: 0,
      pl: 2,
      color: "text.secondary"
    }
  };

  return (
    <Stack spacing={2} sx={{ maxWidth: 980, mx: "auto" }}>
      <Card
        sx={{
          background:
            "linear-gradient(135deg, rgba(130,177,255,0.14), rgba(255,82,82,0.06) 60%, rgba(22,26,34,0.95))"
        }}
      >
        <CardContent sx={{ pb: 2 }}>
          {onBack && (
            <Button startIcon={<ArrowBack />} size="small" onClick={onBack} sx={{ mb: 0.5, px: 0.5 }}>
              {backLabel}
            </Button>
          )}
          <Box display="flex" gap={1} alignItems="center">
            <MenuBookOutlined color="secondary" />
            <Typography variant="h5" fontWeight={750}>
              Documentation
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary" mt={0.5}>
            How the universal solver, screenshot detection, and free-form import work.
          </Typography>
        </CardContent>
        {pages.length > 0 && (
          <Tabs
            value={pageId}
            onChange={(_event, value) => value && setPageId(value)}
            variant={isMobile ? "scrollable" : "standard"}
            scrollButtons="auto"
            sx={{ px: 1, borderTop: "1px solid rgba(255,255,255,0.08)" }}
          >
            {pages.map((page) => (
              <Tab key={page.id} value={page.id} label={page.title} />
            ))}
          </Tabs>
        )}
      </Card>

      {error && <Alert severity="error">{error}</Alert>}

      <Card>
        <CardContent sx={{ px: { xs: 2, sm: 3 } }}>
          {loading ? (
            <Box display="flex" justifyContent="center" py={8}>
              <CircularProgress size={28} />
            </Box>
          ) : (
            <Box sx={markdownSx}>
              {segments.map((segment, index) =>
                segment.type === "mermaid" ? (
                  <MermaidDiagram key={`${pageId}-diagram-${index}`} code={segment.content} />
                ) : (
                  <ReactMarkdown
                    key={`${pageId}-md-${index}`}
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) => {
                        const target = href ? DOC_LINK_TO_PAGE[href.replace(/^\.\//, "")] : undefined;
                        if (target) {
                          return (
                            <a
                              href="#"
                              onClick={(event) => {
                                event.preventDefault();
                                setPageId(target);
                              }}
                            >
                              {children}
                            </a>
                          );
                        }
                        return (
                          <a href={href} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        );
                      }
                    }}
                  >
                    {segment.content}
                  </ReactMarkdown>
                )
              )}
            </Box>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
}
