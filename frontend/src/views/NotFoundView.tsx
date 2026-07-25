import { ArrowBack, HomeOutlined } from "@mui/icons-material";
import { Box, Button, Card, CardContent, Stack, Typography } from "@mui/material";

type NotFoundViewProps = {
  requestedPath: string;
  message?: string;
  onBack: () => void;
  onHome: () => void;
};

export function NotFoundView({
  requestedPath,
  message = "The page you requested does not exist.",
  onBack,
  onHome
}: NotFoundViewProps) {
  return (
    <Box sx={{ maxWidth: 720, mx: "auto", pt: { xs: 4, sm: 8 } }}>
      <Card>
        <CardContent sx={{ py: { xs: 5, sm: 7 }, px: { xs: 3, sm: 6 }, textAlign: "center" }}>
          <Typography
            variant="overline"
            color="primary"
            sx={{ fontSize: "0.9rem", letterSpacing: "0.16em", fontWeight: 800 }}
          >
            404
          </Typography>
          <Typography variant="h3" component="h1" fontWeight={800} mt={0.5}>
            Page not found
          </Typography>
          <Typography color="text.secondary" mt={1.5}>
            {message}
          </Typography>
          <Typography
            component="code"
            display="block"
            sx={{
              mt: 2,
              mx: "auto",
              maxWidth: "100%",
              overflowWrap: "anywhere",
              color: "rgba(255,255,255,0.68)"
            }}
          >
            {requestedPath}
          </Typography>
          <Stack direction={{ xs: "column", sm: "row" }} justifyContent="center" gap={1.5} mt={4}>
            <Button variant="outlined" startIcon={<ArrowBack />} onClick={onBack}>
              Go back
            </Button>
            <Button variant="contained" startIcon={<HomeOutlined />} onClick={onHome}>
              Back to home
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
