import React from "react";
import { createRoot } from "react-dom/client";
import "./styles/global.css";
import {
  createBrowserRouter,
  RouterProvider,
  createRoutesFromElements,
  Route,
} from "react-router-dom";
import App from "./App.tsx";
import ComingSoon from "./pages/ComingSoon.tsx";
import CommTest from "./pages/CommTest.tsx";
import PhotoTest from "./pages/PhotoTest.tsx";
import TrajectoryTest from "./pages/TrajectoryTest.tsx";
import TrajectoryTest2 from "./pages/TrajectoryTest2.tsx";
import TimelineTest from "./pages/TimelineTest.tsx";
import TimelineTest2 from "./pages/TimelineTest2.tsx";
import TimelineTest3 from "./pages/TimelineTest3.tsx";
import VideoTest from "./pages/VideoTest.tsx";

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route path="/" element={<App />}>
      <Route index element={<ComingSoon />} />
      <Route path="comm-test" element={<CommTest />} />
      <Route path="photo-test" element={<PhotoTest />} />
      <Route path="trajectory-test" element={<TrajectoryTest />} />
      <Route path="trajectory-test2" element={<TrajectoryTest2 />} />
      <Route path="timeline-test" element={<TimelineTest />} />
      <Route path="timeline-test2" element={<TimelineTest2 />} />
      <Route path="timeline-test3" element={<TimelineTest3 />} />
      <Route path="video-test" element={<VideoTest />} />
    </Route>
  )
);

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container not found");
}

const root = createRoot(container);
root.render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
