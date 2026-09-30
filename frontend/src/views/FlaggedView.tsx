import { Box, Card, CardContent, Stack, Typography } from "@mui/material";
import { Flag } from "@mui/icons-material";
import { ImageImportEntry } from "../api";
import { ScreenshotArchive } from "../components/ScreenshotArchive";

type FlaggedViewProps = {
  onOpenResult: (name: string, text: string, importId: string) => void;
  onReprocess?: (entries: ImageImportEntry[]) => void;
};

export function FlaggedView({ onOpenResult, onReprocess }: FlaggedViewProps) {
  return (
    <Stack spacing={2} sx={{ maxWidth: 1180, mx: "auto" }}>
      <Card
        sx={{
          // Phones show the page title in the app bar.
          display: { xs: "none", sm: "block" },
          background:
            "linear-gradient(135deg, rgba(255,183,77,0.18), rgba(255,82,82,0.08) 58%, rgba(22,26,34,0.96))"
        }}
      >
        <CardContent>
          <Box display="flex" gap={1.25} alignItems="center">
            <Flag color="warning" />
            <Box>
              <Typography variant="h5" fontWeight={750}>
                Flagged review queue
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Persistent screenshot jobs that need another look, including failures and
                questionable generated solutions.
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <ScreenshotArchive
            flaggedOnly
            onOpenResult={onOpenResult}
            onReprocess={onReprocess}
          />
        </CardContent>
      </Card>
    </Stack>
  );
}
