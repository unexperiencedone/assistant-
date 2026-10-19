import unittest
from unittest import mock

from assistant.publish import youtube, youtube_api

PACKAGE = {"video": r"C:\out\nova-in-its-own-voice.mp4", "thumbnail": "", "title": "My Voice Assistant Graded Itself",
           "description": "How it works.", "chapters": [["00:00", "One sentence"]], "tags": ["voice assistant", "Python"],
           "visibility": "public"}
DESCRIPTION = youtube.full_description(PACKAGE)


def video(title="nova in its own voice", description="hlo viewers #trending", tags=None, privacy="private", vid="abc"):
    return {"id": vid, "snippet": {"title": title, "description": description, "tags": tags or [], "categoryId": "20",
                                   "thumbnails": {}, "channelTitle": "x"},
            "status": {"privacyStatus": privacy, "embeddable": True}}


class Differences(unittest.TestCase):
    def test_exact_is_empty(self):
        self.assertEqual(youtube_api.differences(video(PACKAGE["title"], DESCRIPTION, PACKAGE["tags"]), PACKAGE, DESCRIPTION), [])

    def test_each_wrong_field_is_named(self):
        wrong = youtube_api.differences(video(tags=["voice assistant"]), PACKAGE, DESCRIPTION)
        self.assertTrue(any(w.startswith("title is") for w in wrong))
        self.assertIn("the description doesn't match", wrong)
        self.assertTrue(any("missing: Python" in w for w in wrong))

    def test_tags_youtube_hands_back_sorted_still_match(self):
        shuffled = sorted(PACKAGE["tags"], key=str.lower)[::-1]
        self.assertEqual(youtube_api.differences(video(PACKAGE["title"], DESCRIPTION, shuffled), PACKAGE, DESCRIPTION), [])

    def test_a_misspelled_tag_still_fails(self):
        wrong = youtube_api.differences(video(PACKAGE["title"], DESCRIPTION, ["voice assistant", "python"]), PACKAGE, DESCRIPTION)
        self.assertTrue(any("missing: Python" in w and "unexpected: python" in w for w in wrong))


class FindExisting(unittest.TestCase):
    def test_the_studio_draft_named_after_the_file_is_found(self):
        uploads = [video("Something else", vid="x1"), video("nova in its own voice", vid="draft")]
        with mock.patch.object(youtube_api, "my_uploads", return_value=uploads):
            self.assertEqual(youtube_api.find_existing(PACKAGE)["id"], "draft")

    def test_nothing_matching_means_upload(self):
        with mock.patch.object(youtube_api, "my_uploads", return_value=[video("Free Fire montage")]):
            self.assertIsNone(youtube_api.find_existing(PACKAGE))


class Publish(unittest.TestCase):
    def run_publish(self, readback, visibility_error=None, existing=True):
        calls = []
        current = {"v": video()}

        def set_details(v, package, description):
            calls.append("details")
            current["v"] = readback

        def set_visibility(v, privacy):
            calls.append(f"visibility:{privacy}")
            if visibility_error:
                raise youtube_api.YouTubeError(visibility_error)
            current["v"] = {**current["v"], "status": {"privacyStatus": privacy}}

        with mock.patch.object(youtube_api, "find_existing", return_value=video() if existing else None), \
             mock.patch.object(youtube_api, "upload", side_effect=lambda *a: calls.append("upload") or "new"), \
             mock.patch.object(youtube_api, "get_videos", side_effect=lambda ids: [current["v"]]), \
             mock.patch.object(youtube_api, "set_details", side_effect=set_details), \
             mock.patch.object(youtube_api, "set_thumbnail"), \
             mock.patch.object(youtube_api, "set_visibility", side_effect=set_visibility):
            return youtube_api.publish(PACKAGE, DESCRIPTION), calls

    def test_an_existing_draft_is_finished_not_uploaded_again(self):
        (ok, said), calls = self.run_publish(video(PACKAGE["title"], DESCRIPTION, PACKAGE["tags"]))
        self.assertTrue(ok, said)
        self.assertNotIn("upload", calls)
        self.assertEqual(calls, ["details", "visibility:public"])
        self.assertIn("2 tags checked", said)

    def test_wrong_details_never_go_public(self):
        (ok, said), calls = self.run_publish(video(PACKAGE["title"], "something else", PACKAGE["tags"]))
        self.assertFalse(ok)
        self.assertNotIn("visibility:public", calls)
        self.assertIn("stays private", said)

    def test_a_fresh_upload_locked_private_says_so(self):
        (ok, said), calls = self.run_publish(video(PACKAGE["title"], DESCRIPTION, PACKAGE["tags"]),
                                             visibility_error="forbidden", existing=False)
        self.assertFalse(ok)
        self.assertEqual(calls[0], "upload")
        self.assertIn("audited", said)


class SenderRouting(unittest.TestCase):
    def test_signed_in_goes_through_the_api(self):
        draft = {"extra": {**PACKAGE, "video": __file__}}
        with mock.patch.object(youtube_api, "signed_in", return_value=True), \
             mock.patch.object(youtube_api, "publish", return_value=(True, "done")) as publish, \
             mock.patch.object(youtube, "_studio_sender") as studio:
            self.assertEqual(youtube.sender(draft), (True, "done"))
        publish.assert_called_once()
        studio.assert_not_called()

    def test_not_signed_in_falls_back_to_studio(self):
        draft = {"extra": {**PACKAGE, "video": __file__}}
        with mock.patch.object(youtube_api, "signed_in", return_value=False), \
             mock.patch.object(youtube, "_studio_sender", return_value=(False, "studio")) as studio:
            self.assertEqual(youtube.sender(draft), (False, "studio"))
        studio.assert_called_once()


if __name__ == "__main__":
    unittest.main()
